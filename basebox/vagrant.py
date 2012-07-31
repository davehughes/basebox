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

    def virtualbox(self, vm=None):
        if vm:
            return VirtualBox(VagrantBox(vself, vm=vm))
        else:
            return VirtualBox(self)

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
                # Pick up the first interface if none is specified
                if not iface:
                    firstline = run('ifconfig -s').splitlines()[1]
                    iface = re.split('\s+', firstline)[0]

                output = run('ifconfig %s' % iface)
                m = re.search('inet addr:(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})',
                              output)
                self._ip[(vm, iface)] = m.group(1)
        return self._ip.get((vm, iface))

    def info(self, vm=None):
        info = self.ssh_config(vm=vm)
        info['status'] = self.status(vm=vm)
        info['home'] = self.directory
        info['uuid'] = self.uuid(vm=vm)
        info['ip'] = self.ip(vm=vm)
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
            output = run('vagrant ssh-config %s' % (vm or '',))
            ssh_info = output.splitlines()[1:]
            ssh_info = dict([l.strip().split(' ', 1)
                             for l in ssh_info if l.strip()])
            return {k.lower(): v for k, v in ssh_info.items()}

    def package(self, vm=None, base=None, output=None, include=None,
                vagrantfile=None):
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
                    if vagrantfile is True:
                        # If vagrantfile == True, just use the current VagrantFile
                        cmd += ' --vagrantfile %s' % os.path.join(self.directory, 'Vagrantfile')
                    elif type(getattr(vagrantfile, 'read', None)) == types.FunctionType:
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
        del connections[env.host_string]

        env.clear()
        env.update(self.original_settings)


def vagrant_environment(name, dir='vagrant'):
    '''
    Configure and load the named vagrant environment.
    Usage (with config at ./vagrant/test/environment.json):

        fab vagrant_environment:test [tasks in environment]

    Conventions:
    - Represent a cluster as a JSON file ('environment.json') in a directory
      that can double as a Vagrant directory.
    - Use the cluster spec to project a Vagrantfile and to assign roles to the
      boxes.
    - Load role -> host mappings to initialize fabric roledefs.

    Example environment.json showing all currently supported keys:
    {
        "vms": {
            "web": {                   # box name for vagrant
                "box": "base",         # name or URL of installed base box
                "ip": "192.168.1.10",  # address on host-only network
                "roles": ["app", "redis", "cache"]  # fabric roles for this box
            },
            "db": {
                "box": "base",
                "ip": "192.168.1.11",
                "roles": ["database", "solr"]
            },
            ... etc. ...
        }
    }
    '''
    env_dir = os.path.abspath(os.path.join(dir, name))
    env_config = os.path.join(env_dir, 'environment.json')
    if not file_exists(env_config):
        abort(red('Could not load environment config: %s' % env_config))

    # Load config and bring boxes up
    config = json.loads(file_read(env_config))
    file_render_template('vagrant/Vagrantfile',
                         os.path.join(env_dir, 'Vagrantfile'),
                         context=config)

    env.roledefs = defaultdict(list, env.roledefs)
    for name, vm in (config.get('vms') or {}).items():
        env.hosts.append(vm['ip'])
        for role in vm.get('roles') or []:
            env.roledefs[role].append(vm['ip'])

    with cd(env_dir):
        run('vagrant up')

    # register boxes by role
    import environments
    environments.shared()


class VagrantBox(object):
    def __init__(self, context, box_name=None):
        if isinstance(context, VagrantContext):
            self.context = context
        else:
            self.context = VagrantContext(context)
        self.box_name = box_name

    # Proxy methods to underlying context
    def __getattr__(self, attr):
        f = getattr(self.context, attr)
        return lambda *a, **kw: f(*a, vm=self.box_name, **kw)


class VirtualBox(object):
    '''
    Wraps the 'VBoxManage' shell command to provide low-level control over
    virtual machines.
    '''
    def __init__(self, context):
        self.context = context

    def info(self, details=True):
        '''
        Parse showvminfo output into an attribute map.
        '''
        result = run('VBoxManage showvminfo %s --machinereadable' %
                     self.context.uuid())
        infomap = {}
        pattern = re.compile('^(?P<attribute>.+)=(?:\\"(?P<quoted>.+)\\"|(?P<unquoted>.+))$')

        for line in result.splitlines():
            m = pattern.match(line)
            infomap[m.group('attribute')] = m.group('quoted') or m.group('unquoted')

        return result

    def unregister(self, delete=False):
        cmd = 'VBoxManage unregistervm %s' % self.context.uuid()
        if delete:
            cmd += ' --delete'
        result = run(cmd)
        return result

    def modify(self, **options):
        pass

    def control(self):
        pass
