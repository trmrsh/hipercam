import sys
import os

import hipercam as hcam
from hipercam import cline, utils, spooler
from hipercam.cline import Cline

__all__ = ['tbytes',]

############################################################
#
# tbytes -- strips timing bytes out of a run, writes to disk
#
############################################################

def tbytes(args=None):
    """``tbytes [source] run``

    Reads all timing bytes from a |hiper|, ULTRACAM or ULTRASPEC run
    and dumps them to a disk file. Designed as a safety fallback for
    correcting timing issues where one wants to manipulate the raw
    data.

    Parameters:

        source : string [hidden]
           Data source, two options:

              | 'hl' : local HiPERCAM FITS file
              | 'ul' : ULTRA(CAM|SPEC) server

        run : string
           run number to access, e.g. 'run0034'. This will also be
           used to generate the name for the timing bytes file
           (extension '.tbts'). If a file of this name already exists,
           the script will attempt to read and compare the bytes of
           the two files and report any changes.  The timing bytes
           file will be written to the present working directory, not
           necessarily the location of the data file.

    """

    command, args = utils.script_args(args)

    # get the inputs
    with Cline('HIPERCAM_ENV', '.hipercam', command, args) as cl:

        # register parameters
        cl.register('source', Cline.GLOBAL, Cline.HIDE)
        cl.register('run', Cline.GLOBAL, Cline.PROMPT)

        # get inputs
        source = cl.get_value(
            'source', 'data source [hl, ul]',
            'hl', lvals=('hl','ul')
        )

        run = cl.get_value('run', 'run name', 'run005')
        if run.endswith('.fits'):
            run = run[:-5]

    # create name of timing bytes file
    ofile = os.path.basename(run) + hcam.TBTS

    if source == 'hl':

        nframe = 0
        if os.path.exists(ofile):

            # interpret timing bytes. In this case the file of timing
            # bytes already exists. We read it and the run and compare
            # the times, reporting any differences.
            ndiffer = 0
            with spooler.HcamTbytesSpool(run) as rtbytes:
                with open(ofile,'rb') as fin:
                    for tbytes in rtbytes:
                        otbytes = fin.read(rtbytes.ntbytes)
                        if len(otbytes) != rtbytes.ntbytes:
                            raise hcam.HipercamError(
                                'Failed to read',rtbytes.ntbytes,'bytes from',ofile
                            )
                        nframe += 1

                        if tbytes != otbytes:
                            # now need to interpret times
                            nmjd = h_tbytes_to_mjd(tbytes,nframe)
                            omjd = h_tbytes_to_mjd(otbytes,nframe)
                            print(
                                'Frame {:d}, new vs old GPS timestamp (MJD) = {:.12f} vs {:.12f}'.format(
                                    nframe, nmjd, omjd
                                )
                            )
                            ndiffer += 1

            print('{:s} vs {:s}: there were {:d} time stamp differences in {:d} frames'.format(run,ofile,ndiffer,nframe))

        else:
            # save timing bytes to disk
            with spooler.HcamTbytesSpool(run) as rtbytes:
                with open(ofile,'wb') as fout:
                    for tbytes in rtbytes:
                        fout.write(tbytes)
                        nframe += 1

            print('Found',nframe,'frames in',run,'\nWrote timing data to',ofile)

    elif source == 'ul':

        nframe = 0
        if os.path.exists(ofile):

            # interpret timing bytes. In this case the file of timing
            # bytes already exists. We read it and the run and compare
            # the times, reporting any differences.
            ndiffer = 0
            with spooler.UcamTbytesSpool(run) as rtbytes:
                with open(ofile,'rb') as fin:
                    for tbytes in rtbytes:
                        otbytes = fin.read(rtbytes.ntbytes)
                        if len(otbytes) != rtbytes.ntbytes:
                            raise hcam.HipercamError(
                                'Failed to read',rtbytes.ntbytes,'bytes from',ofile
                            )
                        nframe += 1

                        if tbytes != otbytes:
                            # now need to interpret times
                            nmjd = u_tbytes_to_mjd(tbytes,rtbytes,nframe)
                            omjd = u_tbytes_to_mjd(otbytes,rtbytes,nframe)
                            print(
                                'Frame {:d}, new vs old GPS timestamp (MJD) = {:.12f} vs {:.12f}'.format(
                                    nframe, nmjd, omjd
                                )
                            )
                            ndiffer += 1

            print('{:s} vs {:s}: there were {:d} time stamp differences in {:d} frames'.format(run,ofile,ndiffer,nframe))

        else:
            # no timing bytes files exists; save to disk
            with spooler.UcamTbytesSpool(run) as rtbytes:
                with open(ofile,'wb') as fout:
                    for tbytes in rtbytes:
                        fout.write(tbytes)
                        nframe += 1

            print('Found',nframe,'frames in',run,'\nWrote timing data to',ofile)



def u_tbytes_to_mjd(tbytes, rtbytes, nframe):
    """Translates set of ULTRACAM timing bytes into an MJD"""
    return hcam.ucam.utimer(tbytes,rtbytes,nframe)[1]['gps'].mjd

def h_tbytes_to_mjd(tbytes, nframe):
    """Translates set of HiPERCAM timing bytes into an MJD"""

    # number of seconds in a day
    DAYSEC = 86400.

    frameCount, timeStampCount, years, day_of_year, hours, mins, \
        seconds, nanoseconds, nsats, synced = htimer(tbytes)
    frameCount += 1

    if frameCount != nframe:
        if frameCount == nframe + 1:
            warnings.warn('frame count mis-match; a frame seems to have been dropped')
        else:
            warnings.warn('frame count mis-match; {:d} frames seems to have been dropped'.format(frameCount-self.nframe))

    try:
        imjd = gregorian_to_mjd(years, 1, 1) + day_of_year - 1
        fday = (hours+mins/60+(seconds+nanoseconds/1e9)/3600)/24
    except ValueError:
        imjd = 51544
        fday = nframe/DAYSEC

    return imjd+fday
