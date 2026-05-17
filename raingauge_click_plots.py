#!/usr/local/anaconda3/envs/chil_3_8/bin/python
# =========================================================================
# raingauge_click_plots 
# Displays plots to click on to exclude suspect data 
#
# Author:        Judith Jeffery, RAL 
# Last modified: 26/05/20 
# =========================================================================

import getopt, sys, os
import numpy as np
import netCDF4 as nc4
from datetime import date, datetime
import time
from pylab import date2num
import matplotlib.pyplot as plt
#import matplotlib.patches as patches
from matplotlib.patches import Rectangle
from scipy.integrate import trapz

def find_nearest(array,value):
    idx = (np.abs(array-value)).argmin()
    return array[idx]

# Simple mouse click function to store coordinates
def onclick(event):
    global ix, iy
    ix, iy = event.xdata, event.ydata
    px, py = event.x, event.y

    #print 'x = %d, y = %d'%(ix, iy)
    print(ix, iy)
    print(px, py)

    # assign global variable to access outside of function
    global coords
    global fig_pixels 

    #if np.isreal(ix):   
    #if ix >= x[0]:	#Better to replace with outside x axis min and max   
    if ix >= -1.0 and ix <= 25.0:	#Better to replace with outside x axis min and max   
        coords.append((ix, iy))
        fig_pixels.append((px, py))
    else:
        print('Outside x-axes')
        #fig.canvas.mpl_disconnect(cid)
        #plt.close(1)
    #print(coords)
    ## Disconnect after 4 clicks
    #if len(coords) == 4:
        #fig.canvas.mpl_disconnect(cid)
        #plt.close(1)
    return

# Respond to key press by ending click options 
def onkey(event):
    print('Key pressed')
    #rect = patches.Rectangle((50,100),40,30,linewidth=1,edgecolor='r',facecolor='none')
    #ax.add_patch(rect)
    fig.canvas.mpl_disconnect(bid)
    plt.close(1)
    return

# ------------------------------------------------------------------------
# Define the raingauge read function
# ------------------------------------------------------------------------
def raingauge_read_day(today_date):


    # ----------------------
    # Initialize data arrays
    # ----------------------
    n_logger_gauges = 7
    rg_arr       = -1.0 * np.ones((8640,n_logger_gauges))
    cal_factors  = [0.0033, 0.0033, 0.00189, 0.0033, 0.2, 0.2]
    mean_diam   = np.zeros((127))
    mean_vol    = np.zeros((127))
    ds_bin_width = np.zeros((127))
    mean_th_diam = np.zeros((22))
    th_bin_width = np.zeros((22))
    missing_value = 0.0	#Only used to remove already-corrected data from plots. Using zero means that daily sums are correct
    time_default = (1.0 + np.arange(8640))/360.0        #Default 10s timebase times in hours
    n_times_ch = 8640	#Initial default value
    n_times_sp = 8640

    nday = int(date2num(datetime(int(today_date[0:4]),int(today_date[4:6]),int(today_date[6:8]),0,0,0)))
    epoch_offset = 719163
    disdro_swap_1 = 737916      #20210506, when Sparsholt disdro was swapped into place of Chilbolton disdro

    if nday < 100000:   #Implies 1 Jan 1970 as start of Julian day values
        nday_disdro_test = nday + epoch_offset
    else:       #Implies 1 Jan 0000 as start of Julian day values, as from older version of Python
        nday_disdro_test = nday

    # ----------------------
    # Coefficients for terminal velocity
    # ----------------------
    c0=-0.19305
    c1=0.0049631
    c2=-9.0457E-07
    c3=5.6597E-11
    #Coefficients for <100um diameter (PWS100 only)
    c00=-0.015727
    c01=9.7297E-04
    c02=1.6739E-05
    disdro_A=5.0265E-03 #m2
    #ch_ds_size_um=1000.0*mean_diam
    #ch_ds_tvel=c0+c1*ch_ds_size_um+c2*ch_ds_size_um**2+c3*ch_ds_size_um**3;
    #print ch_ds_tvel    #Check these numbers
    pw_A=4.0E-03        #m2
    th_A=4.65E-03       #m2

    # -------------------------
    # Unix time at start of day 
    # -------------------------
    tt = (int(today_date[0:4]),int(today_date[4:6]),int(today_date[6:8]),0,0,0,0,0,0)
    day_start = time.mktime(tt)

    # -----------
    # Setup paths
    # -----------
    print('Setting up paths')
    sub_path_ch_1 = "/data/amof-netCDF/ncas-rain-gauge-1/ncas-rain-gauge-1_cao_"
    sub_path_ch_2 = "/data/amof-netCDF/ncas-rain-gauge-2/ncas-rain-gauge-2_cao_"
    sub_path_ch_3 = "/data/amof-netCDF/ncas-rain-gauge-3/ncas-rain-gauge-3_cao_"
    sub_path_ch_4 = "/data/amof-netCDF/ncas-rain-gauge-5/ncas-rain-gauge-5_cao_"
    sub_path_ch_5 = "/data/amof-netCDF/ncas-rain-gauge-9/ncas-rain-gauge-9_cao_"
    sub_path_sp_1 = "/data/amof-netCDF/ncas-rain-gauge-7/ncas-rain-gauge-7_cao_"
    sub_path_sp_2 = "/data/amof-netCDF/ncas-rain-gauge-6/ncas-rain-gauge-6_cao_"
    sub_path = [sub_path_ch_1, sub_path_ch_2, sub_path_ch_3, sub_path_ch_4, sub_path_ch_5, sub_path_sp_1, sub_path_sp_2]

    path_ch_pl = "/data/amof-netCDF/ncas-rain-gauge-4/ncas-rain-gauge-4_cao_"
    #path_ch_ds = "/data/netCDF/files/cfarr-disdrometer_chilbolton/cfarr-disdrometer_chilbolton_"
    if nday_disdro_test < disdro_swap_1:
        path_ch_ds = "/data/amof-netCDF/ncas-disdrometer-1/ncas-disdrometer-1_cao_"
    else:
        path_ch_ds = "/data/amof-netCDF/ncas-disdrometer-2/ncas-disdrometer-2_cao_"
    path_sp_ds = "/data/netCDF/files/cfarr-disdrometer_sparsholt/cfarr-disdrometer_sparsholt_"
    #path_ch_pw = "/data/netCDF/files/cfarr-campbell-pws100_chilbolton/cfarr-campbell-pws100_chilbolton_"
    path_ch_pw = "/data/amof-netCDF/ncas-present-weather-1/ncas-present-weather-1_cao_"
    path_out = "/mnt/wilma_home/jla/rain_quality/"

    file_ch_pl = path_ch_pl + today_date + "_precipitation_v1.0.nc"
    #file_ch_ds = path_ch_ds + today_date + ".nc"
    file_ch_ds = path_ch_ds + today_date + "_precipitation_v1.0.nc"
    print('Numeric day, Chilbolton disdrometer file = ',nday_disdro_test, file_ch_ds)
    file_sp_ds = path_sp_ds + today_date + ".nc"
    #file_ch_pw = path_ch_pw + today_date + ".nc"
    file_ch_pw = path_ch_pw + today_date + "_present-weather_v1.0.nc"


    for n in range(n_logger_gauges):
        file_rg = sub_path[n] + today_date + "_precipitation_v1.0.nc"
        print('Raingauge file = ',file_rg)
        if os.path.isfile(file_rg):
            ncfile=nc4.Dataset(file_rg,'r',format='NETCDF4_CLASSIC')
            #ncfile.set_auto_maskandscale(False)
            time_temp=ncfile.variables['time']
            if n <= 4:  #Chilbolton
                ch_rg_time=(time_temp[:] - day_start)/3600.0
                #print(ch_rg_time[0:10])
                #In case there aren't 8640 points in the netCDF file, find the index of the first point
                start_index = np.argmin(time_default - ch_rg_time[0])
                n_times = len(ch_rg_time)
                if n == 0:
                    n_times_ch = n_times
                    #print('n_times_ch = ',n_times_ch)
            else:       #Sparsholt
                sp_rg_time=(time_temp[:] - day_start)/3600.0
                #print(sp_rg_time[0:10])
                #In case there aren't 8640 points in the netCDF file, find the index of the first point
                start_index = np.argmin(time_default - sp_rg_time[0])
                n_times = len(sp_rg_time)
                if n == 5:
                    n_times_sp = n_times
            rain_temp=ncfile.variables['thickness_of_rainfall_amount']
            qc_temp = ncfile.variables['qc_flag']
            vals = rain_temp[:]
            rain_qc = qc_temp[:]
            vals = np.where(rain_qc == 1, vals, missing_value)
            #rg_arr[:,n] = rain_temp[:]
            rg_arr[start_index:n_times,n] = vals	#Note - fill values get reset to 0, even if automask false
            ncfile.close
        else:	#Chilbolton or Sparsholt raingauge file doesn't exist
            if n <= 4:
                ch_rg_time = (10.0 * np.arange(8640) + 10.0)/3600.0
            else:
                sp_rg_time = (10.0 * np.arange(8640) + 10.0)/3600.0

    #rg_arr = np.where(rg_arr >= 0.0, rg_arr, 0.0)

    if os.path.isfile(file_ch_ds):
        #ncfile=nc4.Dataset(file_ch_ds,'r',format='NETCDF3_CLASSIC')
        ncfile=nc4.Dataset(file_ch_ds,'r',format='NETCDF4_CLASSIC')
        time_temp=ncfile.variables['time']
        #ch_ds_time=(time_temp[:])/3600.0
        ch_ds_time=(time_temp[:] - day_start)/3600.0
        temp_ds = ncfile.variables['diameter']
        ch_diam = temp_ds[:]	#mm
        rain_temp=ncfile.variables['number_of_hydrometeors_per_size_channel']
        ch_ds_arr = rain_temp[:]
        ch_ds_arr = ch_ds_arr.transpose()
        #print('ch_ds_arr shape = ', ch_ds_arr.shape)
        ncfile.close

        for n in range(126):
            mean_diam[n]=np.mean([ch_diam[n],ch_diam[n+1]])
            mean_vol[n]=0.5236*np.mean([ch_diam[n]**3,ch_diam[n+1]**3])
            ds_bin_width[n]=ch_diam[n+1]-ch_diam[n]
        mean_diam[126] = 5.203      #arbitrary value based on spacing of previous bins
        mean_vol[126] = 74.0
        ds_bin_width[126] = ds_bin_width[125]       #arbitrary value based on spacing of previous bins

        ch_count_vol_product = mean_vol*np.transpose(ch_ds_arr)
        ch_accum_array = (np.sum(ch_count_vol_product,axis=1))/(disdro_A*1.0e+06)
        #print(ch_accum_array.shape)
        ch_accum_day = np.sum(ch_accum_array)
        #ch_ds_size_um=1000.0*mean_diam
        #ch_ds_tvel=c0+c1*ch_ds_size_um+c2*ch_ds_size_um**2+c3*ch_ds_size_um**3;
        #ch_ds_dsd=(1.0/disdro_A)*(np.transpose(ch_ds_arr))/(ch_ds_tvel*ds_bin_width)
        #ch_ds_daily = np.sum(ch_ds_dsd, axis = 0)
        #ch_ds_dsd=np.log10(np.transpose(ch_ds_dsd))
        #print 'ch_ds_daily shape = ',ch_ds_daily.shape

    else:
        ch_ds_time = 0.0
        ch_accum_array = -1.0 

    if os.path.isfile(file_sp_ds):
        ncfile=nc4.Dataset(file_sp_ds,'r',format='NETCDF3_CLASSIC')
        time_temp=ncfile.variables['time']
        sp_ds_time=(time_temp[:])/3600.0
        temp_ds = ncfile.variables['drop_size']
        sp_diam = temp_ds[:]	#mm
        rain_temp=ncfile.variables['drop_count']
        sp_ds_arr = rain_temp[:]
        sp_ds_arr = sp_ds_arr.transpose()
        #print('sp_ds_arr shape = ', sp_ds_arr.shape)
        ncfile.close

        for n in range(126):
            mean_diam[n]=np.mean([sp_diam[n],sp_diam[n+1]])
            mean_vol[n]=0.5236*np.mean([sp_diam[n]**3,sp_diam[n+1]**3])
            ds_bin_width[n]=sp_diam[n+1]-sp_diam[n]

        sp_count_vol_product = mean_vol*np.transpose(sp_ds_arr)
        sp_accum_array = (np.sum(sp_count_vol_product,axis=1))/(disdro_A*1.0e+06)
        #Note - in IDL prog. we use min volume to calculate accumulation. Here we use mean volume, which is better.
        sp_accum_day = np.sum(sp_accum_array)
        #sp_ds_dsd=(1.0/disdro_A)*(np.transpose(sp_ds_arr))/(ch_ds_tvel*ds_bin_width)
        #sp_ds_daily = np.sum(sp_ds_dsd, axis = 0)
        #sp_ds_dsd=np.log10(np.transpose(sp_ds_dsd))
        #print 'sp_ds_daily shape = ',sp_ds_daily.shape

    else:
        sp_ds_time = 0.0
        sp_accum_array = -1.0
 
    if os.path.isfile(file_ch_pl):
        ncfile=nc4.Dataset(file_ch_pl,'r',format='NETCDF4_CLASSIC')
        time_temp=ncfile.variables['time']
        ch_pl_time=time_temp[:]
        ch_pl_time = (ch_pl_time - day_start)/3600.0
        rain_temp=ncfile.variables['thickness_of_rainfall_amount']
        qc_temp = ncfile.variables['qc_flag_thickness_of_rainfall_amount']
        ch_plrain_arr = rain_temp[:]
        rain_qc = qc_temp[:]
        ch_plrain_arr = np.where(rain_qc == 1, ch_plrain_arr, missing_value)

        ncfile.close
        ch_plrain_sum = np.sum(ch_plrain_arr)

    else:
        ch_pl_time = 0.0
        ch_plrain_arr = -1.0 

    if os.path.isfile(file_ch_pw):
        ncfile=nc4.Dataset(file_ch_pw,'r',format='NETCDF4_CLASSIC')
        time_temp=ncfile.variables['time']
        ch_pw_time=time_temp[:]
        ch_pw_time = (ch_pw_time - day_start)/3600.0
        #ch_pw_time=(time_temp[:])/3600.0
        rain_temp=ncfile.variables['thickness_of_rainfall_amount']
        ch_pwrain_arr = rain_temp[:]   #m to mm
        #Note: the previous line gives WARNING: valid_max not used since it cannot be safely cast to variable data type

    else:
        ch_pw_time = 0.0
        ch_pwrain_arr = -1.0 

    return ch_rg_time, sp_rg_time, rg_arr, ch_pl_time, ch_plrain_arr, ch_ds_time, ch_accum_array, ch_pw_time, ch_pwrain_arr, n_times_ch, n_times_sp	#, sp_ds_time, sp_accum_array

# -------------------------
# Explain function usage
# Needs to be before main()
# -------------------------
def usage():

    print("Command line structure")
    print("\n")
    print("./raingauge_click_plots.py -s yyyymmdd (of start day)")


# ------------------------------------------------------------------------
# Define the raingauge read function
# ------------------------------------------------------------------------
def figure_plotting(yyyymmdd, which_plot, n_plot_arr, corr_time_arr):

    #fig = plt.figure(1, figsize = (17,8))
    fig, axes = plt.subplots(nrows=2, ncols=5, figsize = (17,8))
    fig.suptitle('Raingauge intercomparison ' + yyyymmdd)
    fig.subplots_adjust(wspace = 0.3, hspace = 0.4, left = 0.05, right = 0.97)
    #subplots_adjust(wspace = 0.4, hspace = 0.4)
    #ax = fig.add_subplot(251)
    axes[0,0].plot(ch_rg_time, rg_arr[0:n_times_ch,0], linewidth = 0.3)
    axes[0,0].set_title(plot_title[0], fontsize = 9)
    axes[0,0].set_xlim(-1.0, 25.0)
    axes[0,0].set_ylabel("Accumulation (mm)")
    axes[0,0].set_xticks( 4.0 * np.arange(7) )
    if np.amax(rg_arr[:,0]) <= 0:
        axes[0,0].set_ylim(-0.0001, 0.001)
    if np.amax(rg_arr[:,0] < 0): 
        axes[0,0].text(8.0, 0.0008, 'No data', color = 'r')

    #ax = fig.add_subplot(252)
    axes[0,1].plot(ch_rg_time, rg_arr[0:n_times_ch,1], linewidth = 0.3)
    axes[0,1].set_title(plot_title[1], fontsize = 9)
    axes[0,1].set_xlim(-1.0, 25.0)
    axes[0,1].set_xticks( 4.0 * np.arange(7) )
    if np.amax(rg_arr[:,1]) <= 0:
        axes[0,1].set_ylim(-0.0001, 0.001)
    if np.amax(rg_arr[:,1] < 0): 
        axes[0,1].text(8.0, 0.0008, 'No data', color = 'r')

    #ax = fig.add_subplot(253)
    axes[0,2].plot(ch_rg_time, rg_arr[0:n_times_ch,2], linewidth = 0.3)
    print(np.amin(rg_arr[0:n_times_ch,2]),np.amax(rg_arr[0:n_times_ch,2]))
    axes[0,2].set_title(plot_title[2], fontsize = 9)
    axes[0,2].set_xlim(-1.0, 25.0)
    axes[0,2].set_xticks( 4.0 * np.arange(7) )
    if np.amax(rg_arr[:,2]) <= 0:
        axes[0,2].set_ylim(-0.0001, 0.001)
    if np.amax(rg_arr[:,2] < 0): 
        axes[0,2].text(8.0, 0.0008, 'No data', color = 'r')

    #ax = fig.add_subplot(254)
    axes[0,3].plot(ch_rg_time, rg_arr[0:n_times_ch,3], linewidth = 0.3)
    axes[0,3].set_title(plot_title[3], fontsize = 9)
    axes[0,3].set_xlim(-1.0, 25.0)
    axes[0,3].set_xticks( 4.0 * np.arange(7) )
    #axes[0,3].set_xlabel("Time (hours)")
    if np.amax(rg_arr[:,3]) <= 0:
        axes[0,3].set_ylim(-0.0001, 0.001)
    if np.amax(rg_arr[:,3] < 0): 
        axes[0,3].text(8.0, 0.0008, 'No data', color = 'r')

    #ax = fig.add_subplot(255)
    axes[0,4].plot(ch_rg_time, rg_arr[0:n_times_ch,4], linewidth = 0.3)
    axes[0,4].set_title(plot_title[4], fontsize = 9)
    axes[0,4].set_xlim(-1.0, 25.0)
    axes[0,4].set_xticks( 4.0 * np.arange(7) )
    #axes[0,4].set_xlabel("Time (hours)")
    if np.amax(rg_arr[:,4]) <= 0:
        axes[0,4].set_ylim(-0.0001, 0.001)
    if np.amax(rg_arr[:,4] < 0): 
        axes[0,4].text(8.0, 0.0008, 'No data', color = 'r')

    #ax = fig.add_subplot(256)
    axes[1,0].plot(ch_pl_time, ch_plrain_arr, linewidth = 0.3)
    axes[1,0].set_title(plot_title[5], fontsize = 9)
    axes[1,0].set_xlim(-1.0, 25.0)
    axes[1,0].set_xticks( 4.0 * np.arange(7) )
    axes[1,0].set_xlabel("Time (hours)")
    axes[1,0].set_ylabel("Accumulation (mm)")
    if np.amax(ch_plrain_arr) <= 0:
        axes[1,0].set_ylim(-0.0001, 0.001)
    if np.amax(ch_plrain_arr < 0): 
        axes[1,0].text(8.0, 0.0008, 'No data', color = 'r')

    #ax = fig.add_subplot(257)
    axes[1,1].plot(ch_ds_time, ch_accum_array, linewidth = 0.3)
    axes[1,1].set_title(plot_title[6], fontsize = 9)
    axes[1,1].set_xlim(-1.0, 25.0)
    axes[1,1].set_xticks( 4.0 * np.arange(7) )
    if np.amax(ch_accum_array) <= 0:
        axes[1,1].set_ylim(-0.0001, 0.001)
    axes[1,1].set_xlabel("Time (hours)")
    if np.amax(ch_plrain_arr < 0): 
        axes[1,1].text(8.0, 0.0008, 'No data', color = 'r')

    #ax = fig.add_subplot(258)
    axes[1,2].plot(sp_rg_time, rg_arr[0:n_times_sp,5], linewidth = 0.3, color = 'r')
    axes[1,2].set_title(plot_title[7], fontsize = 9)
    axes[1,2].set_xlim(-1.0, 25.0)
    axes[1,2].set_xticks( 4.0 * np.arange(7) )
    if np.amax(rg_arr[:,5]) <= 0:
        axes[1,2].set_ylim(-0.0001, 0.001)
    axes[1,2].set_xlabel("Time (hours)")
    if np.amax(rg_arr[:,5] < 0): 
        axes[1,2].text(8.0, 0.0008, 'No data', color = 'r')

    #ax = fig.add_subplot(259)
    axes[1,3].plot(sp_rg_time, rg_arr[0:n_times_sp,6], linewidth = 0.3, color = 'r')
    axes[1,3].set_title(plot_title[8], fontsize = 9)
    axes[1,3].set_xlim(-1.0, 25.0)
    axes[1,3].set_xticks( 4.0 * np.arange(7) )
    if np.amax(rg_arr[:,6]) <= 0:
        axes[1,3].set_ylim(-0.0001, 0.001)
    axes[1,3].set_xlabel("Time (hours)")
    if np.amax(rg_arr[:,6] < 0): 
        axes[1,3].text(8.0, 0.0008, 'No data', color = 'r')

    #ax = fig.add_subplot(2510)
    axes[1,4].plot(ch_pw_time, ch_pwrain_arr, linewidth = 0.3)
    axes[1,4].set_title(plot_title[10], fontsize = 9)
    axes[1,4].set_xlim(-1.0, 25.0)
    axes[1,4].set_xticks( 4.0 * np.arange(7) )
    if np.amax(ch_pwrain_arr) <= 0:
        axes[1,4].set_ylim(-0.0001, 0.001)
    axes[1,4].set_xlabel("Time (hours)")
    if np.amax(ch_pwrain_arr < 0): 
        axes[1,4].text(8.0, 0.0008, 'No data', color = 'r')

    #ax = fig.add_subplot(2510)
    #ax.plot(sp_ds_time, sp_accum_array, linewidth = 0.5, color = 'r')
    #ax.set_title(plot_title[9])
    #ax.set_xlim(-1.0, 25.0)
    #ax.set_xticks( 4.0 * np.arange(7) )
    #if np.amax(sp_accum_array) == 0:
        #ax.set_ylim(-0.0001, 0.001)
    #ax.set_xlabel("Time (hours)")
    #ax.plot(x,y)


    if which_plot == 2:
        for n in range(len(n_plot_arr)):
            ax1_val = int(n_plot_arr[n]/5)
            ax2_val = int(n_plot_arr[n]%5)
            y_scale = axes[ax1_val, ax2_val].get_ylim()
            rect = Rectangle((corr_time_arr[0,n],0),(corr_time_arr[1,n]-corr_time_arr[0,n]),y_scale[1]/2.0,linewidth=0.5,edgecolor='r',facecolor='none')
            axes[ax1_val, ax2_val].add_patch(rect)

    return fig














# ------------------------------------------------------------------------
#def main():
# ------------------------------------------------------------------------
if len(sys.argv) <= 1:  #no arguments provided
    usage()
    sys.exit(2)
try:
    opts, args = getopt.getopt(sys.argv[1:], "hs:")
except getopt.GetoptError as err:
    # print help information and exit:
    print(str(err)) # will print something like "option -a not recognized"
    usage()
    sys.exit(2)


# -------------------------
# Set default date as today
# -------------------------
today = date.today()
year  = today.year
month = today.month
day   = today.day


# --------------------
# Command line parsing
# --------------------
for o, a in opts:
        if o in ("-h", "--help"):
            usage()
            sys.exit()
        elif o in ("-s", "--yyyymmdd"):
            yyyymmdd = a
            year   = int(yyyymmdd[0:4])
            month  = int(yyyymmdd[4:6])
        else:
            assert False, "unhandled option"

print("year: ",year)
print("month: ", month)
print("date: ", yyyymmdd)

# ------------------------------------------------------------------------
# Get raingauge data
# ------------------------------------------------------------------------

#plot_title = ['Ch drop count A', 'Ch drop count B', 'Ch drop count C', 'Ch tipping bucket A', 'Ch flux compound drop count', 'Ch weighing', 'Ch disdrometer', 'Sp drop count A', 'Sp tipping bucket A', 'Sp disdrometer', 'Ch Campbell PWS100']
plot_title = ['ncas-rain-gauge-1 rg001dc_ch', 'ncas-rain-gauge-2 rg006dc_ch', 'ncas-rain-gauge-3 rg008dc_ch', 'ncas-rain-gauge-5 rg004tb_ch', 'ncas-rain-gauge-9 rg009dc_ch flux compound', 'ncas-rain-gauge-4 pldc_ch weighing', 'ncas-disdrometer-1 or 2 Ch disdro', 'ncas-rain-gauge-7 rg001dc_sp', 'ncas-rain-gauge-6 rg002tb_sp', 'ncas-disdrometer-2 Sp disdro', 'ncas-present-weather-1 PWS100']
corr_path = '/data/netCDF/corrections/'
#corr_file_id = ['rg001dc_ch', 'rg006dc_ch', 'rg008dc_ch', 'rg004tb_ch', 'rg009dc_ch', 'pldcrg_ch', 'disdrom_ch', 'rg001dc_sp', 'rg002tb_sp', 'disdrom_sp']
corr_file_id = ['rg001dc_ch', 'rg006dc_ch', 'rg008dc_ch', 'rg004tb_ch', 'rg009dc_ch', 'pldcrg_ch', 'disdrom_ch', 'rg001dc_sp', 'rg002tb_sp', 'pws100']
corr_ext = '.corr'
rg_vals = raingauge_read_day(yyyymmdd)

#Returns:
#ch_rg_time, sp_rg_time, rg_arr, ch_pl_time, ch_plrain_arr, ch_ds_time, ch_ds_arr, ch_pw_time, ch_pwrain_arr, sp_ds_time, sp_ds_arr

ch_rg_time = rg_vals[0]
sp_rg_time = rg_vals[1]
rg_arr = rg_vals[2]
ch_pl_time = rg_vals[3]
ch_plrain_arr = rg_vals[4]
ch_ds_time = rg_vals[5]
ch_accum_array = rg_vals[6]
ch_pw_time = rg_vals[7]
ch_pwrain_arr = rg_vals[8]
n_times_ch = rg_vals[9]
n_times_sp = rg_vals[10]
#sp_ds_time = rg_vals[7]
#sp_accum_array = rg_vals[8]
#print(ch_rg_time.shape, rg_arr.shape)

#x = np.arange(-10,10)
#y = x**2
which_plot = 1
n_plot_arr = 0	#Dummy values, not used for the first, clickable plot 
corr_time_arr = 0
click_error = 0	#Flag which is set to 1 if there is an odd number of clicks on the plot or clicks in the wrong time order
#It appears that the actual "click" on the plot is slightly to higher time values than the left edge of the cursor
#click_offset attempts to correct for this
#click_offset = -0.16667 	#10 minutes, in hours
click_offset = -0.08333 	#10 minutes, in hours
fig = figure_plotting(yyyymmdd, which_plot, n_plot_arr, corr_time_arr)

coords = []
fig_pixels = []


# Call click func
#cid = fig.canvas.mpl_connect('button_press_event', onclick)
bid = fig.canvas.mpl_connect('key_press_event', onkey)
cid = fig.canvas.mpl_connect('button_press_event', onclick)

#rect = patches.Rectangle((50,100),40,30,linewidth=1,edgecolor='r',facecolor='none')
#ax.add_patch(rect)
plt.show()	#Was plt.show(1)

#rect = patches.Rectangle((50,100),40,30,linewidth=1,edgecolor='r',facecolor='none')
#ax.add_patch(rect)

#ax = fig.add_subplot(122)
#ax.plot(x,y)

#plt.show(1)


n_clicks = len(coords)	#Number of valid clicks
if n_clicks % 2 != 0:	#An odd number of clicks on the plot, which is ambiguous
    click_error = 1
#print('coords = ', coords)
#print('fig_pixels = ', fig_pixels)
#if n_clicks > 0:
    #print(coords[0][0], coords[1][0], coords[0][1], coords [1][1])

#Check that each pair of clicks has the smaller time value first
for m in range(0, n_clicks, 2):
    if coords[m][0] > coords[m+1][0]:
        print('Coordinates switched: ',m, coords[m][0], coords[m+1][0])
        click_error = 1


if click_error == 0:	#No faults detected

    n_corrs = int(n_clicks/2)
    n_plot_arr = np.zeros(n_corrs)
    corr_time_arr = np.zeros((2,n_corrs))

    # ------------------------------------------------------------------------
    # Designate which figure and convert x coordinates to string time
    # ------------------------------------------------------------------------

    for n in range(n_clicks):
        plot_ind = int(n/2)
        if n % 2 == 0:	#First click in pair decides which plot was clicked
            if fig_pixels[n][1] > 395:	#Top row of plot, was 400 until fig size and margins changed 20210609
                if fig_pixels[n][0] < 350:	#was 420
                    n_plot = 0
                elif fig_pixels[n][0] >= 350 and fig_pixels[n][0] < 680:	#was 420, 730
                    n_plot = 1
                elif fig_pixels[n][0] >= 680 and fig_pixels[n][0] < 1010:	#was 730, 1010
                    n_plot = 2
                elif fig_pixels[n][0] >= 1010 and fig_pixels[n][0] < 1340:	#was 1010, 1290
                    n_plot = 3
                else:
                    n_plot = 4
            else:	#Bottom row of plot
                if fig_pixels[n][0] < 350:
                    n_plot = 5
                elif fig_pixels[n][0] >= 350 and fig_pixels[n][0] < 680:
                    n_plot = 6
                elif fig_pixels[n][0] >= 680 and fig_pixels[n][0] < 1010:
                    n_plot = 7
                elif fig_pixels[n][0] >= 1010 and fig_pixels[n][0] < 1340:
                    n_plot = 8
                else:
                    n_plot = 9
            corr_file = corr_path + corr_file_id[n_plot] + corr_ext
            corr_copy = corr_path + 'corr_copies/' + corr_file_id[n_plot] + '_' +  yyyymmdd[0:6] + corr_ext
            print('plot_ind, n_plot, corr_file, corr_copy = ', plot_ind, n_plot, corr_file, corr_copy)
        temp_time = coords[n][0] + click_offset
        if temp_time >= 0.0 and temp_time < 24.0:
            int_hour = int(np.floor(temp_time))
            if int_hour > 9:
                str_hour = str(int_hour)
            else:
                str_hour = '0' + str(int_hour)
            int_min = int(np.floor((temp_time*60)%60)) 
            if int_min > 9:
                str_min = str(int_min)
            else:
                str_min = '0' + str(int_min)
            int_sec = int(np.floor((temp_time*3600)%60)) 
            if int_sec > 9:
                str_sec = str(int_sec)
            else:
                str_sec = '0' + str(int_sec)

            str_time = str_hour + str_min + str_sec
        elif temp_time < 0.0:
            str_time = '000000'
        else:	#temp_time >= 24.0
            str_time = '235959'
        print('n, temp_time, str_time = ', n, temp_time, str_time)

        if n % 2 == 0:	#First click in pair
            outstr = yyyymmdd  + ' ' + str_time
            n_plot_arr[plot_ind] = n_plot
            corr_time_arr[0,plot_ind] = temp_time
        else:
            outstr = outstr + ' ' + str_time + ' ' + 'HOLDCAL'
            if not os.path.isfile(corr_copy):	#If there isn't already a monthly copy of the correction file
                oscommand = "cp " + corr_file + " " + corr_copy
                #print(oscommand)
                os.system(oscommand)
            corr_out = open(corr_file,'a')
            corr_out.write("%s" % (outstr))
            corr_out.write("%s" % "\n")
            corr_out.close()

            corr_time_arr[1,plot_ind] = temp_time

    which_plot = 2	#Plotting a 2nd time to display removed data
 
    figure_plotting(yyyymmdd, which_plot, n_plot_arr, corr_time_arr)
    print(' ')
    print('##################################################################################')
    print('Corrections successfully written')
    print('##################################################################################')
    print(' ')
    plt.show()

else:	#click_error:
    print(' ')
    print('##################################################################################')
    print('Error in clicking plots')
    print('Either there were an odd number of clicks or the time sequence of clicks was wrong')
    print('No corrections files written')
    print('Please repeat this day') 
    print('##################################################################################')
    print(' ')


