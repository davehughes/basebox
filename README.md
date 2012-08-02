Basebox is a small Python library for building and interacting with [Vagrant](http://vagrantup.com) boxes using [Fabric](http://fabfile.org).  Its goals are somewhat similar to the [veewee](https://github.com/jedi4ever) project, but is specifically geared toward developing and testing Fabric deployments.

Requirements:
-------------
* Vagrant
* VirtualBox

A simple example: the @basebox decorator
----------------------------------------
```python
from fabric.api import sudo
from basebox.build import basebox

@basebox(name='sample', base='http://files.vagrantup.com/precise64.box')
def build_sample_box(*packages):
    for package in packages:
        sudo('apt-get install -y %s' % package)
```
This example performs the following actions:
* Constructs a temporary vagrant box based on ```base```
* Brings the box up, connects to it, and executes the contents of ```build_sample_box()```
* Halts, packages, and installs the box as ```sample```
* Cleans up after itself

Running
```
> fab build_sample_box:nginx,postgresql,rabbitmq-server
```
installs a ```sample``` box with the specified packages preinstalled on it.

The parameters to ```basebox``` can be overridden in the call to the function it decorates, so the following commands will result in an identical box being installed as 'base':
```
> vagrant box add precise64 http://files.vagrantup.com/precise64.box
> fab build_sample_box:nginx,postgresql,rabbitmq-server,name=base,base=precise64
```

Finer grained control with the ```tempbox``` context manager
------------------------------------------------------------
While ```basebox``` instantiates, boots, and connects to its box to execute its context, sometimes this is undesirable.  The ```tempbox``` context manager merely creates a vagrant context with a minimalist Vagrantfile, and cleans up the context upon exiting.  Any actions on the vagrant box, such as booting, connecting, and packaging, must happen through the yielded VagrantContext object ('```box```' in the following example). 
```python
import os

from fabric.api import sudo
from basebox.build import tempbox
from cuisine import mode_local

def build_sample_box(packages=[], name='sample', base='http://files.vagrantup.com/precise64.box'):
    with tempbox(basebox=base) as box:
        box.up()
        with box.connect():
            for package in packages:
                sudo('apt-get install -y %s' % package)
        box.halt()
        box.package(install_as=name)
    
with mode_local():
    build_sample_box(packages=['nginx', 'postgresql', 'rabbitmq-server'])
```
This code is more verbose, but it does essentially the same thing as the @basebox example, and allows more control over the box's lifecycle.  When used in conjunction with the methods for interacting with the underlying VirtualBox, this can enable more advanced build functionality.  This sample packages a box with an alternate NIC (a step I've used when the default NIC was causing network reliability and speed issues):

```python
from basebox.build import tempbox
from cuisine import mode_local

def package_with_alternate_nic(nic, package_out):
    with tempbox() as box:
        # Power-cycle the box to make sure it's instantiated
        box.up()
        box.halt()

        # Run 'VBoxManage modifyvm' to update the box's settings
        box.modify(nictype1=nic)
        
        # Package modified box
        box.package(output=package_out)
        
with mode_local():
    package_with_alternate_nic('virtio', 'virtio.box')
```