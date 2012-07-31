import contextlib

from fabric.api import env
from cuisine import mode_local


@contextlib.contextmanager
def shell_env(**env_vars):
    orig_shell = env['shell']
    env_vars_str = ' '.join('{0}={1}'.format(key, value)
                                       for key, value in env_vars.items())
    env['shell'] = '{0} {1}'.format(env_vars_str, orig_shell)
    yield
    env['shell'] = orig_shell


def default_to_local(f):
    '''
    Decorator that runs its decorated function in local mode if no hosts are
    specified.  Useful for commands that are primarily run on the fabric
    control box, but may also have valid remote uses.

    @default_to_local
    def uname():
        run('uname -a')

    > fab uname  # runs locally without prompting for a host
    '''
    def wrapper(*a, **kw):
        if not env.host_string:
            with mode_local():
                return f(*a, **kw)
        else:
            return f(*a, **kw)
    return wrapper
