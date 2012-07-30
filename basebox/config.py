# TODO: enable these stubbed out config objects to generate Vagrantfiles

class VagrantConfig(object):
    vms = []

    def package(name='package.box'):
        pass

    def vagrant(dotfile_name='.vagrant', host=':detect'):
        pass

    def nfs(map_uid=':auto', map_gid=':auto'):
        pass

    def ssh(username='vagrant', host='127.0.0.1', port=None, guest_port=22,
            max_tries=100, timeout=10, private_key_path=None,
            forward_agent=False, forward_x11=False, shell='bash'):
        pass

    def add_vm(vm):
        pass

    def write(filelike):
        pass


class VagrantVM(object):
    box = None
    box_url = None
    network = None
    guest = ':linux'
    base_mac = None
    boot_mode = ':headless'
    host_name = None

    def __init__(self, name, box, box_url=None):
        pass

    def box(name, url=None):
        pass

    def auto_port_range(min=2250, max=2500):
        pass

    def forward_port(host_port, guest_port, adapter=None, auto=False,
                     protocol=':tcp'):
        pass

    def host_only_network(ip, adapter=None, auto_config=True, mac=None,
                          netmask='255.255.255.0'):
        pass

    def bridged_network(adapter=None, bridge=None, mac=None):
        pass

    def share_folder(name, guest_path, host_path, create=False, nfs=False,
                     transient=False, map_uid=None, map_git=None,
                     nfs_version=None):
        pass

    def write(filelike):
        pass
