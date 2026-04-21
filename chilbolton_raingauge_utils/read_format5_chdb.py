import os

def read_format5_chdb(path_file):
    """
    Reads the format5 channel database (.chdb) file and processes its contents.
    Returns a dictionary where each instrument is a key with a sub-dictionary of its properties.
    """
    # Load the file contents into a list
    channel_info = []
    with open(path_file, 'r') as fid:
        for line in fid:
            channel_info.append(line.rstrip('\n'))

    # Remove leading blanks and lines with no content
    new_channel_info = []
    for line in channel_info:
        stripped_line = line.lstrip()
        if stripped_line:  # Skip empty lines
            new_channel_info.append(stripped_line)

    # Remove lines starting with '#'
    channel_info = [line for line in new_channel_info if not line.startswith('#')]

    # Initialize the channel database dictionary
    chdb = {}

    # Process each line in the channel info
    current_instrument = None
    for line in channel_info:
        parts = line.split()
        key = parts[0]
        if key == 'channel':
            # Start a new instrument entry
            current_instrument = parts[1]
            chdb[current_instrument] = {
                "title": None,
                "location": None,
                "rawrange": None,
                "rawunits": None,
                "realrange": None,
                "realunits": None,
                "interval": None
            }
        elif current_instrument:
            if key == 'title':
                chdb[current_instrument]["title"] = line.replace('title ', '', 1)
            elif key == 'location':
                chdb[current_instrument]["location"] = line.replace('location ', '', 1)
            elif key == 'rawrange':
                chdb[current_instrument]["rawrange"] = {
                    "lower": float(parts[1]),
                    "upper": float(parts[2])
                }
            elif key == 'rawunits':
                chdb[current_instrument]["rawunits"] = line.replace('rawunits ', '', 1)
            elif key == 'realrange':
                chdb[current_instrument]["realrange"] = {
                    "lower": float(parts[1]),
                    "upper": float(parts[2])
                }
            elif key == 'realunits':
                chdb[current_instrument]["realunits"] = line.replace('realunits ', '', 1)
            elif key == 'interval':
                chdb[current_instrument]["interval"] = float(parts[1])
            elif key == 'acquire':
                pass  # No action needed for 'acquire'

    return chdb