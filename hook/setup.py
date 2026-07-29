#!/usr/bin/python3

import os

from setuptools import setup

#Package meta-data
NAME = 'zmes_hook_helpers'
DESCRIPTION = 'ZoneMinder EventServer hook helper functions'
URL = 'https://github.com/ZoneMinder/zmeventnotificationNg/'
AUTHOR_EMAIL = 'pliablepixels@gmail.com'
AUTHOR = 'Pliable Pixels'
LICENSE = 'GPL'
# pyzm[ml] brings Pillow, Shapely, portalocker and onnx. onnx is what reads
# class labels and end2end metadata out of .onnx models. Refs #47.
# numpy stays declared here because zm_detect.py imports it directly;
# scikit-learn and imutils are for pyzm's dlib face training and ALPR.
INSTALL_REQUIRES = [
    'numpy', 'requests', 'imutils',
    'pyzm[ml]>=2.5.0', 'scikit-learn',
    'PyYAML', 'configupdater'
]

here = os.path.abspath(os.path.dirname(__file__))
# read the contents of your README file
with open(os.path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()


def read_version():
    version_file = os.path.join(here, '..', 'VERSION')
    with open(version_file) as f:
        return f.read().strip()


setup(name=NAME,
      version=read_version(),
      description=DESCRIPTION,
      author=AUTHOR,
      author_email=AUTHOR_EMAIL,
      long_description=long_description,
      long_description_content_type='text/markdown',
      url=URL,
      license=LICENSE,
      install_requires=INSTALL_REQUIRES,
      py_modules=[
          'zmes_hook_helpers.common_params', 
          'zmes_hook_helpers.log',
          'zmes_hook_helpers.apigw',
          'zmes_hook_helpers.push',
          'zmes_hook_helpers.utils'
      ])
