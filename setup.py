#!/usr/bin/python
import setuptools

# Load version
execfile('basebox/version.py')

setuptools.setup(
    name='basebox',
    version=__version__,
    url='https://github.com/davehughes/basebox',
    author='David Hughes',
    author_email='d@vidhughes.com',
    description='A small library for interacting with Vagrant boxes using Fabric',

    packages=setuptools.find_packages(),
    install_requires=['fabric', 'cuisine>=0.3.2', 'proxytypes'],
    entry_points={
        'console_scripts': [
            'basebox = basebox.cli:main'
            ]
        }
)
