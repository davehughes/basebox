#!/usr/bin/python
import setuptools
setuptools.setup(
    name='basebox',
    version='0.1.2',
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
