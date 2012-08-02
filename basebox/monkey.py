import ssh
import subprocess
import time

from fabric.api import env, settings
from fabric.state import output
from fabric.operations import (_shell_wrap, _prefix_commands, _prefix_env_vars,
    _sudo_prefix, _AttributeString)
from fabric.io import output_loop
from fabric.thread_handling import ThreadHandler

import cuisine


def patch():
    '''
    Patch cuisine so that mode_local works transparently.

    This implementation mirrors fabric.operations._run_command pretty closely
    and adds correct handling of things like command prefixes, current working
    directory, and output.  Otherwise, common context managers like cd() and
    path() don't apply as expected.

    See also:

        https://github.com/fabric/fabric/blob/master/fabric/operations.py

        https://github.com/sebastien/cuisine/pull/93
    '''
    cuisine.run_local = run_local


def run_local(command, sudo=False, shell=True, pty=True, combine_stderr=None):
    '''Local implementation of fabric.api.run() using subprocess.'''
    return _run_command_local(command, shell, combine_stderr, sudo)


def _run_command_local(command, shell=True, combine_stderr=True, sudo=False,
    user=None):
    '''
    Local implementation of fabric.operations._run_command that uses
    subprocess to execute.
    '''

    # Conditionally import error handling function, since different fabric
    # versions handle this differently
    try:
        from fabric.utils import error
    except ImportError:
        from fabric.operations import _handle_failure
        error = lambda msg=None, **kwargs: _handle_failure(msg)

    # Set up new var so original argument can be displayed verbatim later.
    given_command = command

    # Pick up cuisine sudo mode and password as appropriate
    if sudo and cuisine.sudo_password():
        sudo_prefix = ('echo "%s" | %s -S -p ""' %
            (cuisine.sudo_password, env.sudo_prefix))
    else:
        sudo_prefix = env.sudo_prefix

    # Handle context manager modifications, and shell wrapping
    with settings(sudo_prefix=sudo_prefix):
        wrapped_command = _shell_wrap(
            _prefix_commands(_prefix_env_vars(command), 'remote'),
            shell,
            _sudo_prefix(user) if sudo else None
        )

    # Execute info line
    which = 'sudo' if sudo else 'run'
    if output.debug:
        print("[%s] %s: %s" % ('local', which, wrapped_command))
    elif output.running:
        print("[%s] %s: %s" % ('local', which, given_command))

    # Actual execution, stdin/stdout/stderr handling, and termination
    stdout, stderr, status = _execute_local(wrapped_command, shell=shell,
        combine_stderr=combine_stderr)

    # Assemble output string
    out = _AttributeString(stdout)
    err = _AttributeString(stderr)

    # Error handling
    out.failed = False
    if status != 0:
        out.failed = True
        msg = "%s() received nonzero return code %s while executing" % (
            which, status
        )
        if env.warn_only:
            msg += " '%s'!" % given_command
        else:
            msg += "!\n\nRequested: %s\nExecuted: %s" % (
                given_command, wrapped_command
            )
        error(message=msg, stdout=out, stderr=err)

    # Attach return code to output string so users who have set things to
    # warn only, can inspect the error code.
    out.return_code = status

    # Convenience mirror of .failed
    out.succeeded = not out.failed

    # Attach stderr for anyone interested in that.
    out.stderr = err

    return out


def _execute_local(command, shell=True, combine_stderr=None):
    '''
    Local implementation of fabric.operations._execute using subprocess.
    '''
    if combine_stderr is None:
        combine_stderr = env.combine_stderr
    stderr = subprocess.STDOUT if combine_stderr else subprocess.PIPE

    process = subprocess.Popen(command, shell=shell,
                               stdout=subprocess.PIPE,
                               stderr=stderr)

    # Create handlers to buffer and store output with fabric's output_loop()
    capture_out, capture_err = [], []
    channel = MockChannel(process.stdout, process.stderr)
    workers = (
        ThreadHandler('out', output_loop, channel, "recv", capture_out),
        ThreadHandler('err', output_loop, channel, "recv_stderr", capture_err),
    )

    # Wait for process to finish, raising on any errors
    while process.poll() is None:
        for worker in workers:
            e = worker.exception
            if e:
                raise e[0], e[1], e[2]
        time.sleep(ssh.io_sleep)

    # Join threads to make sure all output was read
    for worker in workers:
        worker.thread.join()

    out = ''.join(capture_out).rstrip('\n')
    err = ''.join(capture_err).rstrip('\n')
    return out, err, process.returncode


class MockChannel(object):
    '''
    Implement just enough of an interface so that we can act as an output
    channel in fabric's output_loop() function.

    Input is not implemented at this time.
    '''
    def __init__(self, stdout, stderr):
        def reader(buf):
            def recv(*args, **kwargs):
                return buf.read(*args, **kwargs)
            return recv if buf else lambda *a, **kw: ''

        self.recv = reader(stdout)
        self.recv_stderr = reader(stderr)
        self.sendall = lambda *a, **kw: None  # no-op
