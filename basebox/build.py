import contextlib
import urlparse
import types
from functools import wraps

from cuisine import *
from fabric.api import env, settings
from fabric.colors import green, red
from fabric.contrib import console
from peak.util.proxies import ObjectProxy
import jinja2
from .vagrant import VagrantBox, installed_boxes
from .util import default_to_local


VFILE_NONE = 0
VFILE_USE_CURRENT = 1
VFILE_COPY_FROM_BASE = 2
VFILE_STRATEGY_MAP = {
    'inherit': VFILE_COPY_FROM_BASE,
    'none': VFILE_NONE,
    'current': VFILE_USE_CURRENT
}


TEMPLATE_ENV = jinja2.Environment(loader=jinja2.ChoiceLoader([
        jinja2.PackageLoader('basebox'),
        jinja2.FileSystemLoader([os.getcwd()])
        ])
    )


def resolve_package_vagrantfile(vfile, box):
    '''
    Resolve `vfile`, which may be either a strategy code, a strategy name, a
    filepath, or a raw string.  Return a string representation of the
    Vagrantfile indicated by the argument.
    '''
    if type(vfile) == types.IntType:
        if vfile in VFILE_STRATEGY_MAP.values():
            strategy = vfile
        else:
            raise ValueError("Invalid vagrantfile strategy code: %s" % vfile)
    elif type(vfile) in types.StringType:
        if vfile in VFILE_STRATEGY_MAP:
            strategy = VFILE_STRATEGY_MAP[vfile]
        elif 'Vagrant::Config' in vfile:
            return vfile
        elif file_exists(vfile):
            return file_read(vfile)

    if strategy == VFILE_COPY_FROM_BASE:
        return file_read(get_inherited_vagrantfile(box.name))
    elif strategy == VFILE_USE_CURRENT:
        return file_read(os.path.join(box.directory, 'Vagrantfile'))
    elif strategy == VFILE_NONE:
        return None
    else:
        raise ValueError('Unresolvable package vagrantfile: %s' % vfile)


def get_inherited_vagrantfile(basebox, vagrant_home='$HOME'):
    basefile = os.path.join(vagrant_home, '.vagrant.d/boxes', basebox, 'include/_Vagrantfile')
    return basefile if file_exists(basefile) else None


class BaseboxDecorator(ObjectProxy):
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

    Some keyword arguments can be provided to customize the build:

     install_as -- What to call the vagrant box when installing it.  Defaults to
                   the name of the decorated function.
     base       -- URL, local path, or name of locally installed vagrant base box.
                   Defaults to 'http://files.vagrantup.com/precise64.box'.
     package_as -- Package output file.
     package_vagrantfile -- Vagrantfile to package with the box.

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
    def __init__(self):
        super(BaseboxDecorator, self).__init__(None)

    def __call__(self, *args, **kwargs):
        def wrap(func):

            @wraps(func)
            @default_to_local
            def wrapper(*a, **kw):

                # If we're already in a nested build, don't rewrap
                if self.__subject__ is not None:
                    return func(*a, **kw)

                # Helper to read parameters from keyword args
                def readarg(arg, default=None):
                    return kw.pop(arg, kwargs.pop(arg, None)) or default

                # Allow overrides in the functions keyword args also
                install_as = readarg('install_as', func.func_name)
                base = readarg('base', 'http://files.vagrantup.com/precise64.box')
                package_vfile = readarg('package_vagrantfile',
                                              VFILE_COPY_FROM_BASE)
                package_as = readarg('package_as')

                # Create a temporary vagrant context, connect to it, and
                # execute the context
                with tempbox(base=base) as box:
                    self.__subject__ = box

                    # Connect to box and execute
                    with box.connect():
                        result = func(*a, **kw)

                    # Determine how to package the box
                    vfile_text = resolve_package_vagrantfile(package_vfile, box)
                    box.package(vagrantfile=vfile_text,
                                install_as=install_as,
                                output=package_as)

                    return result

            return wrapper

        if len(args) == 1 and callable(args[0]):
            return wrap(args[0])
        else:
            return wrap


basebox = BaseboxDecorator()


@contextlib.contextmanager
def tempbox(base='http://files.vagrantup.com/precise64.box',
            vfile_template='Vagrantfile.default',
            vfile_template_context=None):
    '''
    Creates a temporary Vagrant box based on `base`, yielding a VagrantContext,
    then cleans it up after the context executes.

    `base`   -- The base box to build the new box from.  May be one of:
                   + the name of a locally installed box
                   + the path to a local box file
                   + the URL of a remote box file
                   + an instance of Base, which the previous strings get
                     wrapped with anyways
    '''
    base = base if isinstance(base, Base) else Base(base)
    vfile_template = TEMPLATE_ENV.get_template(vfile_template)
    vfile_template_context = vfile_template_context or {}

    # In a temp directory, create, build, and package a basic box
    try:
        base.ensure()
        build_dir = run('mktemp -d')
        print green('Building box in temp directory: %s' % build_dir)

        vagrant = None
        try:
            vagrant = VagrantBox(build_dir)

            # Render the Vagrantfile template
            vfile_template_context.update({'box': base.name, 'box_url': base.url})
            vagrantfile = vfile_template.render(vfile_template_context)
            vagrant.rewrite_vagrantfile(vagrantfile)

            vagrant.basebox = base.name
            yield vagrant
        finally:
            if vagrant:
                try:
                    vagrant.destroy(force=True)
                except:
                    vagrant.unregister(delete=True)
    finally:
        if build_dir:
            print green('Cleaning build directory')
            run('rm -rf %s' % build_dir)
        base.clean()


class Base(object):
    '''
    Given a string representing a box name, file path, or URL, determines
    which one it is and provides methods to temporarily install a box if needed
    and clean it up later.
    '''
    def __init__(self, string):
        self.box_string = string

        if self.box_string in installed_boxes():
            self.name = self.box_string
            self.url = None
            self.installed = True
            self.originally_installed = True
            self.basename = self.box_string
        else:
            self.name = None
            self.url = string
            self.originally_installed = False
            self.installed = False

            if file_exists(string):
                self.basename = os.path.basename(os.path.splitext(string)[0])
            else:  # treat as a URL
                path = urlparse.urlsplit(string).path
                self.basename = os.path.basename(os.path.splitext(path)[0])

    def ensure(self):
        if not self.installed:
            self.name = self.name or self.find_unique_name()
            print green('Installing temporary box: %s' % self.name)
            run('vagrant box add %s %s' % (self.name, self.box_string))
            self.installed = True
        pass

    def clean(self):
        if self.installed and not self.originally_installed:
            print green('Removing temporary box: %s' % self.name)
            run('vagrant box remove %s' % self.name)

    def find_unique_name(self):
        box_name = self.basename
        count = 0
        while box_name in installed_boxes():
            count += 1
            box_name = '%s-%03i' % (self.basename, count)
        return box_name
