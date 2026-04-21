import os
from datetime import datetime

def read_format5_header(path_filename):
    """
    Reads the header information from a format5 data file.
    Converted from MATLAB to Python.
    """
    header = {}

    # Check if the file exists
    if not os.path.exists(path_filename):
        header["present"] = 0
        return header

    header["present"] = 1
    byte_length = os.path.getsize(path_filename)
    comment_count = 0  # Number of comment lines
    hdr_count = 1  # Number of header lines, first one gets skipped

    # Open format5 file in binary mode
    with open(path_filename, "rb") as fid:
        # Get over the comment part of the format5 file
        n = 0
        comment_size = 0
        while n == 0:
            a = fid.readline().decode("utf-8")
            if a.startswith("#"):
                n = 0
                comment_size += len(a)
                comment_count += 1
            else:
                n = 1
        header["comment_size"] = comment_size

        # Read in the header of the format5 file
        n = 0
        header_size = 0
        hdr = []
        while n == 0:
            hdr.append(a.strip())
            header_size += len(a)
            a = fid.readline().decode("utf-8")
            last = len(a)
            if a.startswith("*"):
                n = 0
                hdr_count += 1
            else:
                n = 1
        header["header_size"] = header_size
        header["dataline_size"] = last

        # Extract information from the format5 file
        sensor = 1
        i = 0  # Use an explicit index to iterate through hdr
        while i < len(hdr):
            line = hdr[i]
            if "* descriptor" in line:
                header["descriptor"] = line.replace("* descriptor ", "").strip()
            elif "* database" in line:
                header["database"] = line.replace("* database ", "").strip()
            elif "* sample_interval" in line:
                header["sample_interval"] = line.replace("* sample_interval ", "").strip()
            elif "* chids" in line:
                a = line.replace("* chids ", "").strip()
                if i + 1 < len(hdr) and hdr[i + 1].startswith("* chstat"):
                    b = hdr[i + 1].replace("* chstat ", "").strip()
                    i += 1  # Move to the next line

                    # Split the strings into individual sensor IDs and statuses
                    chids = a.split()
                    chstat = b.split()

                    for chid, chst in zip(chids, chstat):
                        header.setdefault("chids", []).append(chid)
                        header.setdefault("chstat", []).append(chst)
                    sensor += len(chids)
                else:
                    print(f"WARNING: Missing * chstat line after * chids: {line}")
            i += 1  # Move to the next line

        # Record the number of sensors
        header["num_sensors"] = sensor - 1

        # Work out the start timestamp of the format5 file
        year = int(path_filename[-10:-8]) + 2000  # Extract year from the filename
        fid.seek(header["comment_size"] + header["header_size"], os.SEEK_SET)
        line = fid.readline().decode("utf-8").strip()

        try:
            # Split the line by spaces and process the first element (comma-separated timestamp)
            space_split = line.split(" ", 1)  # Split into timestamp and the rest
            timestamp_part = space_split[0]  # Get the first part (comma-separated values)
            parts = timestamp_part.split(",")[:5]  # Extract the first 5 comma-separated values
            time_info = list(map(int, parts))  # Parse the first 5 values as integers
            header["start_ts"] = datetime(year, time_info[0], time_info[1], time_info[2], time_info[3], time_info[4])
        except (ValueError, IndexError):
            print(f"WARNING: Invalid data row format for start timestamp: {line}")
            header["start_ts"] = None

        # Calculate the number of data rows
        header["data_rows"] = (byte_length - header["comment_size"] - header["header_size"]) // header["dataline_size"]

        # Work out the finish timestamp of the format5 file
        fid.seek(-header["dataline_size"], os.SEEK_END)
        line = fid.readline().decode("utf-8").strip()

        try:
            # Split the line by spaces and process the first element (comma-separated timestamp)
            space_split = line.split(" ", 1)  # Split into timestamp and the rest
            timestamp_part = space_split[0]  # Get the first part (comma-separated values)
            parts = timestamp_part.split(",")[:5]  # Extract the first 5 comma-separated values
            time_info = list(map(int, parts))  # Parse the first 5 values as integers
            header["finish_ts"] = datetime(year, time_info[0], time_info[1], time_info[2], time_info[3], time_info[4])
        except (ValueError, IndexError):
            print(f"WARNING: Invalid data row format for finish timestamp: {line}")
            header["finish_ts"] = None

    return header