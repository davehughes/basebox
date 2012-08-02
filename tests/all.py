'''
Tests for building base boxes and executing commands on Vagrant boxes and 
clusters.  This package's code isn't particularly easy to test, so there are
some real caveats to running the tests:

+ The tests execute via a fabric connection (by default, to '$USER@localhost'),
  and this connection may request login credentials.
+ The tests will mostly only affect temporary files and directories, but will
  also install Vagrant test boxes.  These should be 
  automatically cleaned at the end of testing, but some manual cleanup may be
  necessary.
+ These tests take a terribly long time to run!  Since they involve bringing
  VMs up and down, this is more like a suite of integration tests than unit
  tests.
'''
import os
import shutil
import tempfile
import unittest
import uuid

from basebox.vagrant import VagrantBox, VagrantContext
from basebox.build import basebox
from fabric.api import env, settings
from cuisine import mode_local, run, sudo

TEST_BASE_BOX = 'basebox-test'
TEST_BASE_BOX_URL = 'http://files.vagrantup.com/precise64.box'


def ensure_test_base_box():
    with settings(warn_only=True):
        if not TEST_BASE_BOX in run('vagrant box list').splitlines():
            run('vagrant box add %s %s' % (TEST_BASE_BOX, TEST_BASE_BOX_URL))


class TestCase(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.ctx = VagrantContext(self.directory)
        self.ctx.rewrite_vagrantfile(self.vagrantfile)

    def tearDown(self):
        self.ctx.destroy()
        shutil.rmtree(self.directory)


class ExecutionTestCase(unittest.TestCase):
    '''
    Base test case for testing remote execution of a group of tasks.
    Instantiates a vagrant context, brings it up to run all tests, and destroys
    it when finished.
    '''

    vagrantfile = '''
        Vagrant::Config.run do |config|
            config.vm.box = '%s'
        end
    ''' % TEST_BASE_BOX

    @classmethod
    def setUpClass(cls):
        '''Create and launch context'''
        ensure_test_base_box()

        cls.directory = tempfile.mkdtemp()
        cls.ctx = VagrantContext(cls.directory)
        cls.ctx.rewrite_vagrantfile(cls.vagrantfile)
        cls.ctx.up()

    @classmethod
    def tearDownClass(cls):
        '''Destroy and clean up context'''
        cls.ctx.destroy(force=True)
        shutil.rmtree(cls.directory)


class TestSingle(ExecutionTestCase):

    def testConnect(self):
        with self.ctx.connect(), settings(warn_only=True):
            result = run('uname -a')
            self.assertTrue(result.succeeded)

    def testIP(self):
        ip = self.ctx.ip()
        with settings(warn_only=True):
            result = run('ping -c 1 -W 10 %s' % ip)
            self.assertIn('1 packets transmitted, 1 received', result)


class TestMulti(ExecutionTestCase):

    vagrantfile = '''
        Vagrant::Config.run do |config|
            config.vm.box = '%s'

            config.vm.define :box1 do |conf|
                conf.vm.network :bridged, :bridge => 'eth0'
            end

            config.vm.define :box2 do |conf|
                conf.vm.network :bridged, :bridge => 'eth0'
            end
        end
    ''' % TEST_BASE_BOX

    def testListBoxes(self):
        self.assertEqual(set(self.ctx.list_boxes()), set(['box1', 'box2']))

    def testBoxAccess(self):
        '''Access individual boxes with [] lookup'''
        box1 = self.ctx['box1']
        box2 = self.ctx['box2']
        self.assertNotEqual(box1.info()['port'], box2.info()['port'])

    @unittest.skip('Too dependent upon external network settings for now, '
                   'needs work')
    def testIPs(self):
        '''
        This test is somewhat brittle, since it relies on the specifics of
        the base box networking config, which has eth0 NAT'd, but it should
        work for our limited purposes here (making sure that the boxes are
        assigned different IPs on the network and that they can be pinged on
        those IPs)
        '''
        ip1 = self.ctx['box1'].ip(iface='eth1')
        ip2 = self.ctx['box2'].ip(iface='eth1')
        self.assertNotEqual(ip1, ip2)
        
        with settings(warn_only=True):
            for ip in [ip1, ip2]:
                result = run('ping -c 1 -W 10 %s' % ip)
                self.assertIn('1 packets transmitted, 1 received', result)

class TestBaseBoxes(unittest.TestCase):

    def testSimpleBaseBox(self):

        ensure_test_base_box()

        # Build a base box, installing some packages
        boxname = 'test-box-%s' % uuid.uuid4()
        test_packages = ['sl']
        test_file = '~/.basebox-test'
        test_file_content = 'testing box building'

        @basebox(name=boxname, base=TEST_BASE_BOX)
        def buildbox():
            for pkg in test_packages:
                sudo('apt-get install -y %s' % pkg)
            run('echo "%s" > %s' % (test_file_content, test_file))

        buildbox()

        # Ensure that the box got installed
        self.assertIn(boxname, run('vagrant box list').splitlines())

        # Bring up a box based on the newly installed box, and test it for
        # the things we just installed
        tempdir = tempfile.mkdtemp()
        ctx = VagrantContext(tempdir)
        ctx.rewrite_vagrantfile('''
            Vagrant::Config.run do |config|
                config.vm.box = '%s'
            end
        ''' % boxname)

        with ctx.connect(), settings(warn_only=True):
            for pkg in test_packages:
                self.assertIn('installed', run('dpkg -s %s | grep Status' % pkg))

            self.assertEqual(run('cat %s' % test_file), test_file_content)

        # TODO: cleanup - remove base box and derived box

    def testMeta(self):
        # TODO: test meta features like basebox.up(), basebox.halt(), etc.
        pass


if __name__ == "__main__":
    env.host_string = '%s@localhost' % os.environ['USER']
    with mode_local():
        unittest.main()
