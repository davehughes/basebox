import argparse
import logging
import os
import pkg_resources
import re
import StringIO
import sys
import types

from basebox.build import (basebox, tempbox, VFILE_STRATEGY_MAP,
    resolve_package_vagrantfile, TEMPLATE_ENV)
from cuisine import mode_local, mode_remote
import fabric.main
import fabric.state
import ssh


LOG = logging.getLogger('basebox')
LOG.level = logging.WARNING
LOG.addHandler(logging.StreamHandler())


def main(args=None):

    parser = argparse.ArgumentParser(
        add_help=False,
        description='''
Command line utility for packaging and installing vagrant boxes built by
executing fabric tasks.  %(prog)s is implemented as a thin wrapper around
fabric's 'fab' command.  It creates a temporary vagrant environment, configures
fabric to connect to it, then hands off execution to fabric, which runs its
tasks against the environment.  When finished, it packages and/or installs the
built box according to the arguments provided.''')

    meta = parser.add_argument_group(title='optional arguments')
    meta.add_argument('-h', '--help',
        action='store_true',
        help='''Show this help message and exit.  For help with fabric options,
            run 'fab --help'.''')

    meta.add_argument('-V', '--version',
        action='store_true',
        help='''show program's version number and exit'''
        )

    meta.add_argument('--log-level',
        help='Level for logging program output',
        default='warning',
        type=log_level
        )

    # Primary arguments for basebox command.
    main = parser.add_argument_group(title='build arguments',
        description='''Arguments to configure building, installation, and
            packaging'''
        )

    main.add_argument('--base',
        help='''Vagrant base box to build with.  Defaults to vagrant's
            precise64 box, also available at
            https://files.vagrantup.com/precise64.box''',
        default='https://files.vagrantup.com/precise64.box'
        )

    main.add_argument('--vagrantfile-template',
        # help='Jinja template for rendering Vagrantfile to build with',
        help=argparse.SUPPRESS,
        default=TEMPLATE_ENV.get_template('Vagrantfile.default'),
        type=TEMPLATE_ENV.get_template
        )

    main.add_argument('--package-as',
        metavar='PACKAGE_FILE',
        help='Package file to write the build result to.'
        )

    main.add_argument('--package-vagrantfile',
        help='''Specify how/whether to set the output package Vagrantfile. The
            default is 'inherit', which packages with the Vagrantfile of the
            base box if it exists.  Specifying 'none' will create the package
            without a Vagrantfile.  If a file path or raw string is provided,
            its contents will be used as the output package Vagrantfile.
            ''',
        default='inherit',
        type=package_vagrantfile,
        metavar='(VFILE_PATH|VFILE_STRING|inherit|none)'
        )

    main.add_argument('--install-as',
        help='Install the built box to vagrant as BOXNAME',
        metavar='BOXNAME'
        )

    # Arguments shared with fabric
    shared = parser.add_argument_group(title='shared arguments',
        description='''These arguments are arguments to fabric, but
            %(prog)s also uses them to configure its environment.  They are
            passed through to fabric when it executes.''')

    shared.add_argument('-i',
        metavar='PATH',
        help='''Path to SSH private key file.  May be repeated.  Added to the
            generated Vagrantfile as the 'config.ssh.private_key_path' option.
            '''
        ),
    shared.add_argument('-u', '--user',
        help='''Username to use when connecting to vagrant hosts.  Added to
            the generated Vagrantfile as the 'config.ssh.username' option.
            '''
        ),
    shared.add_argument('--port',
        help='''SSH connection port.  Added to the generated Vagrantfile as the
            'config.ssh.port' option'''
        )

    # OK, this one doesn't do anything extra, but we still want to sanity-check
    # the fabfile before executing.
    shared.add_argument('-f', '--fabfile', help=argparse.SUPPRESS)

    # The 'hosts' parameter works a bit differently in basebox - each hostname
    # must be a valid identifier name and can be assigned roles by listing them
    # in the format 'host:role1,role2'.
    #
    # >>> basebox -H box1:web box2:db,cache
    main.add_argument('-H', '--hosts',
        help='',
        nargs='+',
        type=host_with_roles,
        action='append'
        )

    args, fab_args = parser.parse_known_args(args=args)

    # Set log level so everything after this can emit proper logs
    LOG.level = args.log_level

    # Remove the separator from the fabric arguments
    if '--' in fab_args:
        fab_args.remove('--')

    # Flatten host-role entries and map bidirectionally
    host_roles = {}
    roledefs = {}
    for host, roles in [tpl for sublist in args.hosts for tpl in sublist]:
        if roles:
            LOG.info('Host <%s> specified with roles: %s' % (host, roles))
        else:
            LOG.info('Host <%s> specified with no roles.' % host)

        host_roles[host] = roles
        for role in roles:
            roledefs.setdefault(role, []).append(host)

    # Add hosts to fabric args
    fab_args[:0] = ['--hosts', ','.join(host_roles.keys())]

    # Duplicate shared args back into the fabric argument list
    for action in shared._group_actions:
        if getattr(args, action.dest, None):
            flag = ('-%s' if len(action.dest) == 1 else '--%s') % action.dest
            fab_args[:0] = [flag, getattr(args, action.dest)]

    LOG.info('Checking fabric parameters: %s' % fab_args)

    if args.help:
        parser.print_help()
    elif args.version:
        print_version()
    else:
        if not (args.install_as or args.package_as):
            print 'No action specified (you should use --install-as or --package-as).'
        else:
            # Replace sys.argv to simulate calling fabric as a CLI script
            argv_original = sys.argv
            stderr_original = sys.stderr
            sys.argv = ['fab'] + fab_args
            sys.stderr = StringIO.StringIO()

            # Sanity-check fabric arguments before doing anything heavy
            try:
                fabric.main.parse_options()
            except SystemExit:
                print ('An error was encountered while trying to parse the '
                        'arguments to fabric:')
                print ''
                print os.linesep.join('\t%s' % line for line in
                                      sys.stderr.getvalue().splitlines()[2:])
                print ''
                print 'Please check the syntax and try again.'
                raise
            finally:
                sys.stderr = stderr_original

            # Check fabfile resolution
            fabfile_original = fabric.state.env.fabfile
            if args.fabfile:
                fabric.state.env.fabfile = args.fabfile
            if not fabric.main.find_fabfile():
                print ("Fabric couldn't find any fabfiles! (You may want to "
                    "change directories or specify the -f option)")
                raise SystemExit
            fabric.state.env.fabfile = fabfile_original

            # Render input Vagrantfile with appropriate context
            vfile_ctx = {
                'base': args.base,
                'hosts': host_roles.keys(),
                'ssh': {
                    'username': args.user,
                    'private_key_path': args.i,
                    'port': args.port
                    }
                }

            mode_local()
            with tempbox(base=args.base, 
                         vfile_template=args.vagrantfile_template,
                         vfile_template_context=vfile_ctx) as default_box:

                context = default_box.context

                # Fabricate an SSH config for the vagrant environment and add
                # it to fabric.state.env
                ssh_conf = ssh.SSHConfig()
                ssh_configs = ssh_conf._config
            
                # Add a host entry to SSH config for each vagrant box
                for box_name in context.list_boxes():
                    box = context[box_name]
                    box.up()

                    ssh_settings = box.ssh_config()
                    ssh_settings.update({
                        'host': box_name,
                        'stricthostkeychecking': 'no'
                        })
                    ssh_configs.append(ssh_settings)

                fabric.api.settings.use_ssh_config = True
                fabric.api.env._ssh_config = ssh_conf

                # Configure roledefs
                fabric.state.env.roledefs = roledefs

                # Hand execution over to fabric
                with mode_remote():
                    try:
                        fabric.main.main()
                    except SystemExit as e:  # Raised when fabric finishes.
                        if e.code is not 0:
                            LOG.error('Fabric exited with error code %s' % e.code)
                            raise
                        pass

                vfile = resolve_package_vagrantfile(args.package_vagrantfile)
                context.package(vagrantfile=vfile,
                                install_as=args.install_as,
                                output=args.package_as)

            sys.argv = argv_original


def print_version():
    from .version import __version__
    print 'basebox %s' % __version__
    for pkg in ['fabric', 'ssh', 'cuisine']:
        try:
            dist = pkg_resources.get_distribution(pkg)
            print dist
        except pkg_resources.DistributionNotFound:
            print '%s is missing!' % pkg


#---------------------------------------------------
#  Custom argparse type conversions and validations
#---------------------------------------------------
def package_vagrantfile(string):
    '''
    Validator/converter for the --package-vagrantfile option.
    '''
    strategy = VFILE_STRATEGY_MAP.get(string)
    if strategy:
        return strategy

    if os.path.isfile(string):
        return string

    raise ValueError("must be a valid strategy or filename.")


def host_with_roles(string):
    '''
    Converts a string like 'host:role1,role2' to a tuple of
    ('host', ['role1', 'role2']).
    '''
    host_string, _, roles_string = string.partition(':')
    if not re.match('^\w+$', host_string):
        raise ValueError('Invalid host: %s' % host_string)
    roles = [role for role in re.split('\s*,\s*', roles_string) if role]
    return (host_string, roles)


def log_level(string):
    level = getattr(logging, string.upper(), None)
    if type(level) == types.IntType:
        return level

    try:
        return int(string)
    except ValueError:
        pass

    raise ValueError("Couldn't coerce %s to a valid log level" % string)


if __name__ == '__main__':
    main()
