from collections import defaultdict
import contextlib
import copy
import json
import os
import re
import tempfile
import types

from fabric.api import *
from fabric.colors import *
from cuisine import run, file_exists, is_local, mode_remote, mode_local
from .util import shell_env


class VagrantContext(object):
    _uuid = {}
    _ip = {}

    def __init__(self, directory=None):
        self.directory = os.path.abspath(directory or run('pwd'))
        self.host_string = env.host_string
        self.execmode = mode_local if is_local() else mode_remote
        self.loglevel = 'ERROR'

    @contextlib.contextmanager
    def execution_context(self, loglevel=None):
        loglevel = loglevel or self.loglevel
        with cd(self.directory), settings(host_string=self.host_string):
            with shell_env(VAGRANT_LOG=loglevel), self.execmode():
                yield self

    def uuid(self, vm=None):
        '''
        Determine the underlying VM's UUID, which is useful for purposes like
        performing low-level control tasks via VBoxManage.
        '''
        if not self._uuid.get(vm):
            runfile = os.path.join(self.directory, '.vagrant')
            if not file_exists(runfile):
                self.up(vm=vm)

            runinfo = json.load(open(runfile, 'r'))
            self._uuid[vm] = runinfo['active'].get(vm or 'default')

        return self._uuid.get(vm)

    def ip(self, vm=None, iface=None):
        if not self._ip.get((vm, iface)):
            with self.connect(vm=vm):

                def get_ip(ifc):
                    output = run('ifconfig %s' % ifc)
                    m = re.search('inet addr:(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})',
                                  output)
                    return m.group(1)

                # Pick up the first interface if none is specified
                ip = None
                if iface:
                    ip = get_ip(iface)
                else:
                    for iface in run('ifconfig -s | cut -d" " -f 1 -').splitlines()[1:]:
                        _ip = get_ip(iface)
                        if _ip not in ['10.0.2.15', '127.0.0.1']:
                            ip = _ip
                            break

                self._ip[(vm, iface)] = ip
        return self._ip.get((vm, iface))

    def info(self, vm=None):
        info = self.ssh_config(vm=vm)
        info['status'] = self.status(vm=vm)
        info['home'] = self.directory
        info['uuid'] = self.uuid(vm=vm)
        info['ip'] = self.ip(vm=vm)
        info['vm'] = self.vminfo(vm=vm)
        return info

    def list_boxes(self):
        return self.status().keys()

    def __getitem__(self, idx):
        '''Use [] lookup to retrieve individual boxes by name.'''
        if idx in self.list_boxes():
            return VagrantBox(self, box_name=idx)
        raise KeyError(idx)

    def up(self, *args, **kwargs):
        result = self._up('vagrant up', *args, **kwargs)
        self.uuid(vm=kwargs.get('vm'))  # cache UUID
        return result

    def reload(self, *args, **kwargs):
        return self._up('vagrant reload', *args, **kwargs)

    def halt(self, *args, **kwargs):
        return self._down('vagrant halt', *args, **kwargs)

    def destroy(self, *args, **kwargs):
        return self._down('vagrant destroy', *args, **kwargs)

    def _up(self, cmd, vm=None, provision=False, provision_with=None):
        with self.execution_context():
            if vm:
                cmd += ' ' + vm
            cmd += ' --%sprovision' % ('' if provision else 'no-',)
            if provision_with:
                if not type(provision_with) == types.ListType:
                    provision_with = list(provision_with)
                cmd += (' --provision-with=%s' % ['"%s"' % x for x
                                                  in provision_with].join(','))
            result = run(cmd)
            if result.failed:
                raise Exception(result)
            else:
                return result

    def _down(self, cmd, vm=None, force=False):
        with self.execution_context(), settings(warn_only=True):
            if force:
                cmd += ' --force'
            result = run(cmd)
            if result.failed:
                raise Exception(result)
            else:
                return result

    def ssh_config(self, vm=None, host=None):
        with self.execution_context():
            # load info about the box to use as its context var
            with settings(warn_only=True):
                output = run('vagrant ssh-config %s' % (vm or '',))
                if output.failed:
                    modes = run('ls -la /usr/local/jenkins/jobs/build-vagrant-boxes/workspace/credentials/fabric_rsa')
                    abort(modes + self.read_vagrantfile() + output.stdout + output.stderr)

            ssh_info = output.splitlines()[1:]
            ssh_info = dict([l.strip().split(' ', 1)
                             for l in ssh_info if l.strip()])
            return {k.lower(): v for k, v in ssh_info.items()}

    def package(self, vm=None, base=None, output=None, include=None,
                vagrantfile=None, install_as=None):
        with self.execution_context():
            cmd = 'vagrant package %s' % (vm or '',)
            tmpfile = None
            try:
                if base:
                    cmd += ' --base %s' % base
                if output:
                    cmd += ' --output %s' % output
                if include:
                    cmd += ' --include %s' % ','.join("%s" % i for i in include),
                if vagrantfile:
                    if type(getattr(vagrantfile, 'read', None)) == types.FunctionType:
                        # If  the specified vagrantfile appears to be a 
                        # readable filelike, write it to a temp file and
                        # add to the command.
                        fd, tmpfile = tempfile.mkstemp()
                        os.fdopen(fd, 'w').write(vagrantfile.read())
                        cmd += ' --vagrantfile %s' % tmpfile
                    elif type(vagrantfile) in types.StringTypes:
                        if 'Vagrant::Config' in vagrantfile:
                            # If this appears to be the string representation
                            # of a Vagrantfile, write it to a temp file and 
                            # add it to the command.
                            fd, tmpfile = tempfile.mkstemp()
                            os.fdopen(fd, 'w').write(vagrantfile)
                            cmd += ' --vagrantfile %s' % tmpfile
                            
                        else:
                            # Otherwise, treat it as a filename
                            cmd += ' --vagrantfile %s' % vagrantfile

                run(cmd)

                # Install locally if a target is specified
                if install_as:
                    package_file = output or 'package.box'

                    # Overwrite any existing box with the target name
                    if install_as in run('vagrant box list').splitlines():
                        print red('Removing existing box: %s' % install_as)
                        run('vagrant box remove %s' % install_as)

                    print green('Installing box: %s' % install_as)
                    run('vagrant box add %s %s' %
                        (install_as, os.path.join(self.directory, package_file)))

            finally:
                if tmpfile:
                    os.unlink(tmpfile)

    def resume(self, vm=None):
        with self.execution_context():
            return run('vagrant resume %s' % (vm or '',))

    def suspend(self, vm=None):
        with self.execution_context():
            return run('vagrant suspend %s' % (vm or '',))

    def status(self, vm=None):
        with self.execution_context():
            lines = run('vagrant status %s' % (vm or '',)).splitlines()

            # The output has some presentation text around the states we're
            # interested in, so we have to extract the appropriate lines.
            start, end = [idx for idx, x in enumerate(lines) if x == ''][:2]
            status_parts = [re.split('\s+', line)
                            for line in lines[start + 1:end]]
            status_map = {x[0]: ' '.join(x[1:]) for x in status_parts}
            return status_map.get(vm) if vm else status_map

    def connect(self, vm=None, **ssh_config_overrides):
        '''Context manager that sets the vagrant box as the current host'''
        return _VagrantConnectionManager(self, vm=vm, **ssh_config_overrides)

    def _connection_settings(self, vm=None, **ssh_config_overrides):
        host = 'vagrant-temporary-%s' % self.uuid(vm=vm)

        # Extract current SSH settings
        self.up(vm=vm)
        ssh_settings = self.ssh_config(vm=vm)
        ssh_settings.update({
            'host': host,
            'stricthostkeychecking': 'no'
            })
        ssh_settings.update(ssh_config_overrides)

        # Ensure that SSH config is being picked up and update it with the
        # connection settings for the vagrant box
        with settings(use_ssh_config=True):
            from fabric.network import ssh_config
            ssh_config('test')  # ensure that ssh config is in use and cached
            modified_config = copy.deepcopy(env._ssh_config)
            modified_config._config.append(ssh_settings)

        return {
            'use_ssh_config': True,
            'host': host,
            'host_string': '%(user)s@%(host)s:%(port)s' % ssh_settings,
            '_ssh_config': modified_config,
            'cwd': '',
            'path': ''
            }

    def rewrite_vagrantfile(self, contents, vm=None):
        open(os.path.join(self.directory, 'Vagrantfile'), 'w').write(contents)
        # self.connect(vm=vm)

    def read_vagrantfile(self, vm=None):
        return open(os.path.join(self.directory, 'Vagrantfile')).read()


    # ----------------------------------------------------------------------
    # Low-level interface to boxes - wraps the 'VBoxManage' shell command to 
    # provide low-level control over virtual machines.
    # ----------------------------------------------------------------------

    def vminfo(self, vm=None, details=True):
        '''
        Parse showvminfo output into an attribute map.
        '''
        with self.execution_context():
            result = run('VBoxManage showvminfo %s --machinereadable' %
                         self.uuid(vm=vm))
        infomap = {}
        pattern = re.compile('^(?P<attribute>.+)=(?:\\"(?P<quoted>.+)\\"|(?P<unquoted>.+))$')

        for line in result.splitlines():
            m = pattern.match(line)
            infomap[m.group('attribute')] = m.group('quoted') or m.group('unquoted')

        return infomap

    def unregister(self, vm=None, delete=False):
        cmd = 'VBoxManage unregistervm %s' % self.uuid(vm=vm)
        if delete:
            cmd += ' --delete'
        with self.execution_context():
            result = run(cmd)
            return result

    def modify(self, vm=None, **options):
        uuid = self.uuid(vm=vm)
        opts = ['--%s %s' % (k, v) for k, v in options.iteritems()]
        with self.execution_context():
            cmd = 'VBoxManage modifyvm %s %s' % (uuid, ' '.join(opts))
            run(cmd)

    def control(self, command, paramstring=None, vm=None):
        with self.execution_context():
            cmd = ('VBoxManage controlvm %s %s %s' % 
                    (self.uuid(vm=vm), command, paramstring or ''))
            run(cmd)


class _VagrantConnectionManager(object):

    def __init__(self, context, vm=None, **ssh_config_overrides):
        overrides = context._connection_settings(vm=vm, **ssh_config_overrides)
        self.original_settings = dict(env)
        env.update(overrides)
        mode_remote()

    def __enter__(self, *args, **kwargs):
        pass

    def __exit__(self, *args, **kwargs):
        # Clear any cached connections - vagrant boxes are more transient
        # than other connections, and there are cases where caching connections
        # leads to mistakenly reusing stale connections.
        from fabric.state import connections
        if env.host_string in connections:
            del connections[env.host_string]

        env.clear()
        env.update(self.original_settings)


class VagrantBox(object):
    def __init__(self, context, box_name=None):
        if isinstance(context, VagrantContext):
            self.context = context
        else:
            self.context = VagrantContext(context)

        # Strip boxname when dealing with the default box
        if box_name == 'default':
            self.box_name = None
        else:
            self.box_name = box_name

    # Proxy methods to underlying context
    def __getattr__(self, attr):
        f = getattr(self.context, attr)
        return lambda *a, **kw: f(*a, vm=self.box_name, **kw)
