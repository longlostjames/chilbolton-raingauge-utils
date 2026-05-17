#!/usr/bin/env python3
"""
Interactive point-and-click tool for identifying and flagging pump cycle
artefacts in raingauge NetCDF files.

Displays drop counts for a processed raingauge NetCDF file.  Click on any
sample to toggle its QC flag between good (1) and pump cycle (2).  The plot
redraws immediately to reflect the change.

Controls
--------
  Left-click on a spike  — toggle QC flag for that sample
  Auto-detect            — run the 12-hour periodicity detector and apply flags
  Clear all              — remove all pump cycle flags
  Save & quit            — write updated QC flags back to the NetCDF, then exit
  Discard & quit         — exit without saving

Usage
-----
    identify-pump-cycles  <netcdf_file>  [-o <output_file>]
"""

import argparse
import sys
import numpy as np
import pandas as pd
import netCDF4 as nc4
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.widgets as mwidgets

try:
    from .process_raingauge import detect_pump_cycles
except ImportError:
    from chilbolton_raingauge_utils.process_raingauge import detect_pump_cycles


class PumpCyclePicker:
    FLAG_GOOD = np.int8(1)
    FLAG_PUMP = np.int8(2)

    def __init__(self, nc_path, out_path=None):
        self.nc_path = nc_path
        self.out_path = out_path or nc_path
        self._load()
        self._build_ui()

    # ------------------------------------------------------------------
    # Data I/O
    # ------------------------------------------------------------------

    def _load(self):
        with nc4.Dataset(self.nc_path) as nc:
            unix = nc.variables['time'][:].data.copy()
            self.drops = nc.variables['number_of_drops'][:].data.astype(float)
            if 'qc_flag' in nc.variables:
                self.qc = nc.variables['qc_flag'][:].data.astype(np.int8).copy()
            else:
                self.qc = np.ones(len(unix), dtype=np.int8)

        self.times = pd.to_datetime(unix, unit='s')
        self.time_num = mdates.date2num(self.times.to_pydatetime())
        self.n = len(self.times)
        self._original_qc = self.qc.copy()

    def _save(self):
        with nc4.Dataset(self.out_path, 'r+') as nc:
            if 'qc_flag' in nc.variables:
                nc.variables['qc_flag'][:] = self.qc
            else:
                v = nc.createVariable('qc_flag', 'i1', ('time',), fill_value=-127)
                v.units = '1'
                v.long_name = 'Data Quality flag'
                v.flag_values = np.array([0, 1, 2, 3, 4], dtype=np.int8)
                v.flag_meanings = (
                    'not_used good_data instrument_error '
                    'bad_data_precipitation_rate_less_than_0_mm_hr-1 '
                    'suspect_data_precipitation_rate_greater_than_300_mm_hr-1'
                )
                v[:] = self.qc
        n_flagged = int((self.qc == self.FLAG_PUMP).sum())
        print(f"Saved to {self.out_path}  ({n_flagged} sample(s) flagged as pump cycle)")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        nc_name = self.nc_path.split('/')[-1]

        self.fig = plt.figure(figsize=(16, 6))
        try:
            self.fig.canvas.manager.set_window_title(f'Pump Cycle Picker — {nc_name}')
        except AttributeError:
            pass

        # Main plot area
        self.ax = self.fig.add_axes([0.05, 0.18, 0.93, 0.74])

        # Buttons — auto / clear on the left, save/quit on the right
        self.btn_auto  = mwidgets.Button(
            self.fig.add_axes([0.05, 0.03, 0.13, 0.08]), 'Auto-detect')
        self.btn_clear = mwidgets.Button(
            self.fig.add_axes([0.20, 0.03, 0.13, 0.08]), 'Clear all')
        self.btn_save  = mwidgets.Button(
            self.fig.add_axes([0.70, 0.03, 0.13, 0.08]), 'Save & quit',
            color='#c8e6c9', hovercolor='#a5d6a7')
        self.btn_discard = mwidgets.Button(
            self.fig.add_axes([0.85, 0.03, 0.13, 0.08]), 'Discard & quit',
            color='#ffcdd2', hovercolor='#ef9a9a')

        self.btn_auto.on_clicked(self._on_auto)
        self.btn_clear.on_clicked(self._on_clear)
        self.btn_save.on_clicked(self._on_save_quit)
        self.btn_discard.on_clicked(self._on_discard_quit)

        # Status text between the button groups
        self._status_ax = self.fig.add_axes([0.35, 0.03, 0.33, 0.08])
        self._status_ax.axis('off')
        self._status_text = self._status_ax.text(
            0.5, 0.5, '', ha='center', va='center',
            fontsize=9, transform=self._status_ax.transAxes)

        # Instruction annotation
        self.fig.text(0.05, 0.96,
                      'Left-click a spike to flag/unflag it as a pump cycle artefact',
                      fontsize=8, color='dimgray')

        self.fig.canvas.mpl_connect('button_press_event', self._on_click)

        self._redraw()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _redraw(self):
        self.ax.cla()

        good = self.qc != self.FLAG_PUMP
        pump = self.qc == self.FLAG_PUMP

        # Good samples — blue vertical lines from zero
        self.ax.vlines(self.time_num[good], 0, self.drops[good],
                       colors='steelblue', linewidth=0.8, alpha=0.8)

        # Flagged samples — red vertical lines + cross markers
        if pump.any():
            self.ax.vlines(self.time_num[pump], 0, self.drops[pump],
                           colors='red', linewidth=1.2, zorder=4)
            self.ax.scatter(self.time_num[pump],
                            np.maximum(self.drops[pump], self.ax.get_ylim()[1] * 0.02 + 0.05),
                            color='red', marker='x', s=80, linewidths=1.8,
                            zorder=5, label='Pump cycle (QC=2)')

        date_label = self.times[0].strftime('%Y-%m-%d')
        n_pump = int(pump.sum())
        self.ax.set_title(
            f'Drop counts — {date_label}   ({n_pump} sample{"s" if n_pump != 1 else ""} flagged)',
            fontsize=11)
        self.ax.set_ylabel('Drops per 10 s')
        self.ax.set_xlabel('Time (UTC)')
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        self.ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        self.ax.xaxis.set_minor_locator(mdates.HourLocator(interval=1))
        self.ax.set_xlim(self.time_num[0], self.time_num[-1])
        self.ax.grid(True, alpha=0.3)
        if pump.any():
            self.ax.legend(loc='upper right', fontsize=9)

        self._status_text.set_text(
            f'{n_pump} sample{"s" if n_pump != 1 else ""} flagged as pump cycle')
        self.fig.canvas.draw_idle()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_click(self, event):
        """Toggle the QC flag of the sample nearest to the click position."""
        if event.inaxes is not self.ax:
            return
        if event.xdata is None:
            return
        # Only respond to left mouse button (button 1)
        if event.button != 1:
            return

        idx = int(np.argmin(np.abs(self.time_num - event.xdata)))
        t = self.times[idx].strftime('%H:%M:%S')

        if self.qc[idx] == self.FLAG_PUMP:
            self.qc[idx] = self.FLAG_GOOD
            print(f"  Unflagged sample {idx} ({t}), drops={int(self.drops[idx])}")
        else:
            self.qc[idx] = self.FLAG_PUMP
            print(f"  Flagged sample {idx} ({t}), drops={int(self.drops[idx])}")

        self._redraw()

    def _on_auto(self, _event):
        """Apply the 12-hour periodicity detector and update flags."""
        mask = detect_pump_cycles(self.drops)
        # Replace only pump-cycle flags, leave any other flag values intact
        self.qc[mask] = self.FLAG_PUMP
        self.qc[~mask & (self.qc == self.FLAG_PUMP)] = self.FLAG_GOOD
        print(f"  Auto-detect: {int(mask.sum())} sample(s) flagged")
        self._redraw()

    def _on_clear(self, _event):
        """Remove all pump cycle flags."""
        n = int((self.qc == self.FLAG_PUMP).sum())
        self.qc[self.qc == self.FLAG_PUMP] = self.FLAG_GOOD
        print(f"  Cleared {n} pump cycle flag(s)")
        self._redraw()

    def _on_save_quit(self, _event):
        self._save()
        plt.close(self.fig)

    def _on_discard_quit(self, _event):
        self.qc = self._original_qc
        print("Changes discarded.")
        plt.close(self.fig)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Interactively identify and flag pump cycle artefacts "
            "in a raingauge NetCDF file."
        )
    )
    parser.add_argument('nc_file', help='Processed raingauge NetCDF file to inspect')
    parser.add_argument(
        '-o', '--output', default=None,
        help='Output NetCDF file path (default: overwrite input file)')
    args = parser.parse_args()

    picker = PumpCyclePicker(args.nc_file, out_path=args.output)
    plt.show()


if __name__ == '__main__':
    main()
