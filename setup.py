#!/usr/bin/python
import setuptools
setuptools.setup(
    name='basebox',
    version='0.1.0',
    packages = setuptools.find_packages(),
    install_requires = ['fabric', 'cuisine', 'proxytypes'],
)

