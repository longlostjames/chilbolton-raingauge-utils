"""
Chilbolton Raingauge Utils
===========================
Processing utilities for Chilbolton raingauge data.

Provides tools to convert raw Campbell Scientific CR1000X datalogger files
and legacy Format5 binary files containing RAL Drop Counting Gauge data into
CF-compliant NetCDF files, with quality control flagging and quicklook plots.
"""
__version__ = "1.0.0"
__author__ = "Chris Walden"

from .process_raingauge import main as process_raingauge_main
from .process_raingauge_f5 import main as process_raingauge_f5_main
from .process_raingauge_stfc import main as process_raingauge_stfc_main
from .read_format5_header import read_format5_header
from .read_format5_content import read_format5_content
from .split_cr1000x_data_daily import main as split_cr1000x_data_daily_main
from .make_quicklooks import main as make_quicklooks_main
from .read_format5_chdb import read_format5_chdb

__all__ = [
    "process_raingauge_main",
    "process_raingauge_f5_main",
    "process_raingauge_stfc_main",
    "read_format5_header",
    "read_format5_content",
    "split_cr1000x_data_daily_main",
    "make_quicklooks_main",
    "read_format5_chdb",
]
