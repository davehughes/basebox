import contextlib
import urlparse

from fabric.api import env, settings
from fabric.colors import green, red
from fabric.contrib import console
from cuisine import *
from peak.util.proxies import ObjectProxy
from .vagrant import VagrantBox
from .util import default_to_local


class BaseBox(ObjectProxy):
    '''
    Decorator class for functions that build base boxes.  For instance, the
    following example:

    @basebox
    def mybox():
        install_nginx()

    performs the following steps:

     + creates a disposable vagrant box based on the default base box
     + runs the decorated function (in this case executing 'install_nginx')
     + packages and installs the box as 'mybox'

    Two keyword arguments can be provided to customize the build:

     name    -- What to call the vagrant box when installing it.  Defaults to
                the name of the decorated function.
     base    -- URL, local path, or name of locally installed vagrant base box.
                Defaults to 'http://files.vagrantup.com/precise64.box'.

    Additionally, the @basebox decorator exposes the operations and information
    of the box it is building via an instance of VagrantContext, so wrapped
    functions can manipulate the state of the box even though it is temporary
    and anonymous:

    @basebox
    def advanced():
        # ... run some tasks ...
        basebox.reload()
        # ... run other tasks that required a restart ...
    '''
    def __call__(self, *args, **kwargs):
        def wrap(func):

            @default_to_local
            def wrapper(*a, **kw):

                # If we're already in a nested build, don't rewrap
                if self.__subject__ is not None:
                    return func(*a, **kw)

                # Helper to read parameters from keyword args
                def readarg(arg, default=None):
                    return kw.pop(arg, kwargs.pop(arg, None)) or default

                # Allow overrides in the functions keyword args also
                name = readarg('name', func.func_name)
                base = readarg('base', 'http://files.vagrantup.com/precise64.box')
                package_with_vagrantfile = readarg('package_with_vagrantfile',
                                                   False)

                with build_and_install_box(name,
                    basebox=base,
                    package_with_vagrantfile=package_with_vagrantfile
                    ) as box:
                    self.__subject__ = box
                    return func(*a, **kw)
            return wrapper

        if len(args) == 1 and callable(args[0]):
            return wrap(args[0])
        else:
            return wrap

basebox = BaseBox(None)


@contextlib.contextmanager
def build_and_install_box(target, 
                          basebox='http://files.vagrantup.com/precise64.box',
                          package_with_vagrantfile=False,
                          force=True):
    '''
    Builds a Vagrant box based on `base`, executing buildfunc(*args, *kwargs)
    to configure it, then packages it and installs it as `target`.

    `target`    -- Name for the installed box
    `basebox`   -- The base box to build the new box from.  May be one of:
                   + the name of a locally installed box
                   + the path to a local box file
                   + the URL of a remote box file

    `force`     -- Force replacement if the target box is already installed
    '''
    temporary_box = None
    with settings(warn_only=True):
        output = run('vagrant box list')
        installed_boxes = output.splitlines() if output.succeeded else []

    base_installed = basebox in installed_boxes

    if not base_installed:
        # install box temporarily
        if file_exists(basebox):
            basename = os.path.basename(os.path.splitext(basebox)[0])
        else:  # treat as a url
            path = urlparse.urlsplit(basebox).path
            basename = os.path.basename(os.path.splitext(path)[0])

        # find a unique name
        box_name = basename
        count = 0
        while box_name in installed_boxes:
            count += 1
            box_name = '%s-%03i' % (basename, count)

        print green('Installing temporary box: %s' % box_name)
        run('vagrant box add %s %s' % (box_name, basebox))
        temporary_box = box_name
    else:
        box_name = basebox

    # In a temp directory, create, build, and package a basic box
    try:
        build_dir = run('mktemp -d')
        print green('Building box in temp directory: %s' % build_dir)

        vagrant = None
        try:
            vagrantfile = '''
                Vagrant::Config.run do |config|
                    config.vm.box = "%(box)s"
                end
                ''' % {'box': box_name}

            vagrant = VagrantBox(build_dir)
            vagrant.rewrite_vagrantfile(vagrantfile)
            with vagrant.connect():
                yield vagrant

            vagrant.package(output='package.box',
                            vagrantfile=package_with_vagrantfile)
        finally:
            if vagrant:
                try:
                    vagrant.destroy(force=True)
                except:
                    vagrant.virtualbox().unregister(delete=True)

        # If the box needs to be replaced, prompt the user
        if target in run('vagrant box list').splitlines():
            if (not force and
                not console.confirm(("A box named '%s' is already installed.  "
                                    "Do you want to replace it?") % target,
                                    default=False)):
                abort(red('Aborting box installation'))
            print red('Removing existing box: %s' % target)
            run('vagrant box remove %s' % target)

        print green('Installing box: %s' % target)
        run('vagrant box add %s %s' %
            (target, os.path.join(build_dir, 'package.box')))
    finally:
        if build_dir:
            print green('Cleaning build directory')
            run('rm -rf %s' % build_dir)

        if temporary_box:
            print green('Removing temporary box: %s' % temporary_box)
            run('vagrant box remove %s' % temporary_box)
