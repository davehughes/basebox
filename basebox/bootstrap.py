from cuisine import *


def vagrant_install():
    ruby_install()
    virtualbox_install()
    rvm_install_gem('vagrant', gemset='global')


def virtualbox_install(package='virtualbox-4.1', os_version='precise'):

    # Add Oracle's VirtualBox repository and key, and comment out the 'deb-src'
    # line, since they don't provide source.
    vbox_list = '/etc/apt/sources.list.d/virtualbox.list'
    vbox_repo_line = 'deb http://download.virtualbox.org/virtualbox/debian %s contrib' % os_version

    with mode_sudo():
        file_ensure(vbox_list)
        sig = file_sha256(vbox_list)
        file_update(vbox_list, lambda x: text_ensure_line(x, vbox_repo_line))
        if file_sha256(vbox_list) != sig:
            run('curl http://download.virtualbox.org/virtualbox/debian/oracle_vbox.asc | sudo apt-key add -')
            package_update()

    # Update and install packages
    package_ensure('linux-headers-%s' % run('uname -r'))
    package_ensure('dkms')
    package_ensure(package)
