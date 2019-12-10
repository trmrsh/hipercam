import sys
import multiprocessing
import numpy as np
import warnings

import hipercam as hcam
from hipercam import cline, utils, spooler, fitting
from hipercam.cline import Cline
from hipercam.reduction import (
    Rfile, initial_checks, update_plots,
    ProcessCCDs, setup_plots, setup_plot_buffers,
    LogWriter, moveApers
)

# get hipercam version to write into the reduce log file
from pkg_resources import get_distribution, DistributionNotFound
try:
    hipercam_version = get_distribution('hipercam').version
except DistributionNotFound:
    hipercam_version = 'not found'

__all__ = ['reduce', ]


################################################
#
# reduce -- reduces multi-CCD imaging photometry
#
################################################
def reduce(args=None):
    """``reduce [source] rfile (run first last (trim [ncol nrow]) twait
    tmax | flist) log lplot implot (ccd nx msub xlo xhi ylo yhi iset
    (ilo ihi | plo phi))``

    Reduces a sequence of multi-CCD images, plotting lightcurves as images
    come in. It can extract with either simple aperture photometry or Tim
    Naylor's optimal photometry, on specific targets defined in an aperture
    file using |setaper|.

    reduce can source data from both the ULTRACAM and HiPERCAM servers, from
    local 'raw' ULTRACAM and HiPERCAM files (i.e. .xml + .dat for ULTRACAM, 3D
    FITS files for HiPERCAM) and from lists of HiPERCAM '.hcm' files. If you
    have data from a different instrument you should convert into the
    FITS-based hcm format.

    reduce is primarily configured from a file with extension ".red". This
    contains a series of directives, e.g. to say how to re-position and
    re-size the apertures. An initial reduce file is best generated with
    the script |genred| after you have created an aperture file. This contains
    lots of help on what to do.

    A reduce run can be terminated at any point with ctrl-C without doing
    any harm. You may often want to do this at the start in order to adjust
    parameters of the reduce file.

    Parameters:

        source : string [hidden]
           Data source, five options:

             |  'hs': HiPERCAM server
             |  'hl': local HiPERCAM FITS file
             |  'us': ULTRACAM server
             |  'ul': local ULTRACAM .xml/.dat files
             |  'hf': list of HiPERCAM hcm FITS-format files

           'hf' is used to look at sets of frames generated by 'grab' or
           converted from foreign data formats.

        rfile : string
           the "reduce" file, i.e. ASCII text file suitable for reading by
           ConfigParser. Best seen by example as it has many parts.

        run : string [if source ends 's' or 'l']
           run number to access, e.g. 'run034'

        first : int [if source ends 's' or 'l']
           first frame to reduce. 1 = first frame; set = 0 to always try to
           get the most recent frame (if it has changed).

        last : int [if source ends 's' or 'l', hidden]
           last frame to reduce. 0 to just continue until the end.  This is
           not prompted for by default and must be set explicitly.  It
           defaults to 0 if not set. Its purpose is to allow accurate
           profiling tests.

        trim : bool [if source starts with 'u']
           True to trim columns and/or rows off the edges of windows nearest
           the readout. This is particularly for ULTRACAM windowed data where
           the first few rows and columns can contain bad data.

        ncol : int [if trim]
           Number of columns to remove (on left of left-hand window, and right
           of right-hand windows)

        nrow : int [if trim]
           Number of rows to remove (bottom of windows)

        twait : float [if source ends 's'; hidden]
           time to wait between attempts to find a new exposure, seconds.

        tmax : float [if source ends 's'; hidden]
           maximum time to wait between attempts to find a new exposure,
           seconds.

        flist : string [if source ends 'f']
           name of file list

        log : string
           log file for the results

        tkeep : float
           maximum number of minutes of data to store in internal buffers, 0
           for the lot. When large numbers of frames are stored, performance
           can be slowed (although I am not entirely clear why) in which case
           it makes sense to lose the earlier points (without affecting the
           saving to disk). This parameter also gives operation similar to that
           of "max_xrange" parameter in the ULTRACAM pipeline whereby just
           the last few minutes are shown.

        lplot : bool
           flag to indicate you want to plot the light curve. Saves time not
           to especially in high-speed runs.

        implot : bool
           flag to indicate you want to plot images.

        ccd : string [if implot]
           CCD(s) to plot, '0' for all, '1 3' to plot '1' and '3' only, etc.

        nx : int [if implot]
           number of panels across to display.

        msub : bool [if implot]
           subtract the median from each window before scaling for the
           image display or not. This happens after any bias subtraction.

        xlo : float [if implot]
           left-hand X-limit for plot

        xhi : float [if implot]
           right-hand X-limit for plot (can actually be < xlo)

        ylo : float [if implot]
           lower Y-limit for plot

        yhi : float [if implot]
           upper Y-limit for plot (can be < ylo)

        iset : string [if implot]
           determines how the intensities are determined. There are three
           options: 'a' for automatic simply scales from the minimum to the
           maximum value found on a per CCD basis. 'd' for direct just takes
           two numbers from the user. 'p' for percentile dtermines levels
           based upon percentiles determined from the entire CCD on a per CCD
           basis.

        ilo : float [if implot and iset='d']
           lower intensity level

        ihi : float [if implot and iset='d']
           upper intensity level

        plo : float [if implot and iset='p']
           lower percentile level

        phi : float [if implot and iset='p']
           upper percentile level

    .. Warning::

       The transmission plot generated with reduce is not reliable in the
       case of optimal photometry since it is highly correlated with the
       seeing. If you are worried about the transmission during observing,
       you should always use normal aperture photometry.

    """

    command, args = utils.script_args(args)

    with Cline('HIPERCAM_ENV', '.hipercam', command, args) as cl:

        # register parameters
        cl.register('source', Cline.GLOBAL, Cline.HIDE)
        cl.register('rfile', Cline.GLOBAL, Cline.PROMPT)
        cl.register('run', Cline.GLOBAL, Cline.PROMPT)
        cl.register('first', Cline.LOCAL, Cline.PROMPT)
        cl.register('last', Cline.LOCAL, Cline.HIDE)
        cl.register('trim', Cline.GLOBAL, Cline.PROMPT)
        cl.register('ncol', Cline.GLOBAL, Cline.HIDE)
        cl.register('nrow', Cline.GLOBAL, Cline.HIDE)
        cl.register('twait', Cline.LOCAL, Cline.HIDE)
        cl.register('tmax', Cline.LOCAL, Cline.HIDE)
        cl.register('flist', Cline.LOCAL, Cline.PROMPT)
        cl.register('log', Cline.GLOBAL, Cline.PROMPT)
        cl.register('tkeep', Cline.GLOBAL, Cline.PROMPT)
        cl.register('lplot', Cline.LOCAL, Cline.PROMPT)
        cl.register('implot', Cline.LOCAL, Cline.PROMPT)
        cl.register('ccd', Cline.LOCAL, Cline.PROMPT)
        cl.register('nx', Cline.LOCAL, Cline.PROMPT)
        cl.register('msub', Cline.GLOBAL, Cline.PROMPT)
        cl.register('iset', Cline.GLOBAL, Cline.PROMPT)
        cl.register('ilo', Cline.GLOBAL, Cline.PROMPT)
        cl.register('ihi', Cline.GLOBAL, Cline.PROMPT)
        cl.register('plo', Cline.GLOBAL, Cline.PROMPT)
        cl.register('phi', Cline.LOCAL, Cline.PROMPT)
        cl.register('xlo', Cline.GLOBAL, Cline.PROMPT)
        cl.register('xhi', Cline.GLOBAL, Cline.PROMPT)
        cl.register('ylo', Cline.GLOBAL, Cline.PROMPT)
        cl.register('yhi', Cline.GLOBAL, Cline.PROMPT)

        # get inputs
        source = cl.get_value(
            'source', 'data source [hs, hl, us, ul, hf]',
            'hl', lvals=('hs', 'hl', 'us', 'ul', 'hf')
        )

        # set some flags
        server_or_local = source.endswith('s') or source.endswith('l')

        # the reduce file
        rfilen = cl.get_value(
            'rfile', 'reduce file', cline.Fname('reduce.red', hcam.RED))
        try:
            rfile = Rfile.read(rfilen)
        except hcam.HipercamError as err:
            # abort on failure to read as there are many ways to get reduce
            # files wrong
            print(err, file=sys.stderr)
            print('*** reduce aborted')
            exit(1)

        if server_or_local:
            resource = cl.get_value('run', 'run name', 'run005')
            first = cl.get_value('first', 'first frame to reduce', 1, 0)
            cl.set_default('last',0)
            last = cl.get_value('last', 'last frame to reduce', 0, 0)
            if last and last < first:
                print('Cannot set last < first unless last == 0')
                print('*** reduce aborted')
                exit(1)

            if source.startswith('u'):
                trim = cl.get_value(
                    'trim', 'do you want to trim edges of windows? (ULTRACAM only)', True
                )
                if trim:
                    ncol = cl.get_value(
                        'ncol', 'number of columns to trim from windows', 0)
                    nrow = cl.get_value(
                        'nrow', 'number of rows to trim from windows', 0)
            else:
                trim = False

            twait = cl.get_value(
                'twait', 'time to wait for a new frame [secs]', 1., 0.)
            tmx = cl.get_value(
                'tmax', 'maximum time to wait for a new frame [secs]',
                10., 0.)

        else:
            resource = cl.get_value(
                'flist', 'file list', cline.Fname('files.lis', hcam.LIST)
            )
            first = 1
            last = 0
            trim = False

        log = cl.get_value(
            'log', 'name of log file to store results',
            cline.Fname('reduce.log', hcam.LOG, cline.Fname.NEW)
        )

        tkeep = cl.get_value(
            'tkeep', 'number of minute of data to'
            ' keep in internal buffers (0 for all)',
            0., 0.
        )

        lplot = cl.get_value(
            'lplot', 'do you want to plot light curves?', True
        )

        implot = cl.get_value(
            'implot', 'do you want to plot images?', True
        )

        if implot:

            # define the panel grid. first get the labels and maximum
            # dimensions
            ccdinf = spooler.get_ccd_pars(source, resource)

            try:
                nxdef = cl.get_default('nx')
            except KeyError:
                nxdef = 3

            if len(ccdinf) > 1:
                ccd = cl.get_value('ccd', 'CCD(s) to plot [0 for all]', '0')
                if ccd == '0':
                    ccds = list(ccdinf.keys())
                else:
                    ccds = ccd.split()

                if len(ccds) > 1:
                    nxdef = min(len(ccds), nxdef)
                    cl.set_default('nx', nxdef)
                    nx = cl.get_value('nx', 'number of panels in X', 3, 1)
                else:
                    nx = 1
            else:
                nx = 1
                ccds = list(ccdinf.keys())

            # define the display intensities
            msub = cl.get_value(
                'msub', 'subtract median from each window?', True)

            iset = cl.get_value(
                'iset', 'set intensity a(utomatically),'
                ' d(irectly) or with p(ercentiles)?',
                'a', lvals=['a', 'd', 'p']
            )

            plo, phi = 5, 95
            ilo, ihi = 0, 1000
            if iset == 'd':
                ilo = cl.get_value('ilo', 'lower intensity limit', 0.)
                ihi = cl.get_value('ihi', 'upper intensity limit', 1000.)
            elif iset == 'p':
                plo = cl.get_value(
                    'plo', 'lower intensity limit percentile',
                    5., 0., 100.)
                phi = cl.get_value(
                    'phi', 'upper intensity limit percentile',
                    95., 0., 100.)

            # region to plot
            for i, cnam in enumerate(ccds):
                nxtot, nytot, nxpad, nypad = ccdinf[cnam]
                if i == 0:
                    xmin, xmax = float(-nxpad), float(nxtot + nxpad + 1)
                    ymin, ymax = float(-nypad), float(nytot + nypad + 1)
                else:
                    xmin = min(xmin, float(-nxpad))
                    xmax = max(xmax, float(nxtot + nxpad + 1))
                    ymin = min(ymin, float(-nypad))
                    ymax = max(ymax, float(nytot + nypad + 1))

            xlo = cl.get_value('xlo', 'left-hand X value', xmin, xmin, xmax)
            xhi = cl.get_value('xhi', 'right-hand X value', xmax, xmin, xmax)
            ylo = cl.get_value('ylo', 'lower Y value', ymin, ymin, ymax)
            yhi = cl.get_value('yhi', 'upper Y value', ymax, ymin, ymax)

        else:
            ccds, nx, msub, iset = None, None, None, None
            ilo, ihi, plo, phi = None, None, None, None
            xlo, xhi, ylo, yhi = None, None, None, None

        # save list of parameter values for writing to the reduction file
        plist = cl.list()

    ################################################################
    #
    # all the inputs have now been obtained. Get on with doing stuff
    if implot:
        plot_lims = (xlo, xhi, ylo, yhi)
    else:
        plot_lims = None

    imdev, lcdev, spanel, tpanel, xpanel, ypanel, lpanel = setup_plots(
        rfile, ccds, nx, plot_lims, implot, lplot
    )

    # a couple of initialisations
    total_time = 0   # time waiting for new frame

    if lplot:
        lbuffer, xbuffer, ybuffer, tbuffer, sbuffer = setup_plot_buffers(rfile)
    else:
        lbuffer, xbuffer, ybuffer, tbuffer, sbuffer = None, None, None, None, None

    ############################################
    #
    # open the log file and write headers
    #
    with LogWriter(log, rfile, hipercam_version, plist) as logfile:

        ncpu = rfile['general']['ncpu']
        if ncpu > 1:
            pool = multiprocessing.Pool(processes=ncpu)
        else:
            pool = None

        # whether a tzero has been set
        tzset = False

        # containers for the processed and raw MCCD groups
        # and their frame numbers
        pccds, mccds, nframes = [], [], []

        ##############################################
        #
        # Finally, start winding through the frames
        #

        with spooler.data_source(source, resource, first, full=False) as spool:

            # 'spool' is an iterable source of MCCDs
            for nf, mccd in enumerate(spool):

                if server_or_local:

                    # Handle the waiting game ...
                    give_up, try_again, total_time = spooler.hang_about(
                        mccd, twait, tmx, total_time
                    )

                    if give_up:
                        # Giving up, but need to handle any partially filled
                        # frame group

                        if len(mccds):
                            # finish processing remaining frames. This step
                            # will only occur if we have at least once passed
                            # to later stages during which read and gain will
                            # be set up
                            results = processor(pccds, mccds, nframes)

                            # write out results to the log file
                            alerts = logfile.write_results(results)

                            # print out any accumulated alert messages
                            if len(alerts):
                                print('\n'.join(alerts))

                            update_plots(
                                results, rfile, implot, lplot, imdev,
                                lcdev, pccd, ccds, msub, nx, iset, plo, phi,
                                ilo, ihi, xlo, xhi, ylo, yhi, tzero,
                                lpanel, xpanel, ypanel, tpanel, spanel,
                                tkeep, lbuffer, xbuffer, ybuffer, tbuffer,
                                sbuffer
                            )
                            mccds = []

                        print('reduce finished')
                        break

                    elif try_again:
                        continue

                # Trim the frames: ULTRACAM windowed data has bad
                # columns and rows on the sides of windows closest to
                # the readout which can badly affect reduction. This
                # option strips them.
                if trim:
                    hcam.ccd.trim_ultracam(mccd, ncol, nrow)

                # indicate progress
                if 'NFRAME' in mccd.head:
                    nframe = mccd.head['NFRAME']
                else:
                    nframe = nf + 1

                if source != 'hf' and last and nframe > last:
                    # finite last frame number

                    if len(mccds):
                        # finish processing remaining frames
                        results = processor(pccds, mccds, nframes)

                        # write out results to the log file
                        alerts = logfile.write_results(results)

                        # print out any accumulated alert messages
                        if len(alerts):
                            print('\n'.join(alerts))

                        update_plots(
                            results, rfile, implot, lplot, imdev, lcdev,
                            pccd, ccds, msub, nx, iset, plo, phi, ilo, ihi,
                            xlo, xhi, ylo, yhi, tzero, lpanel, xpanel,
                            ypanel, tpanel, spanel, tkeep, lbuffer,
                            xbuffer, ybuffer, tbuffer, sbuffer
                        )
                        mccds = []

                    print(
                        '\nHave reduced up to the last frame set.'
                    )
                    print('reduce finished')
                    break

                print(
                    'Frame {:d}: {:s} [{:s}]'.format(
                        nframe, mccd.head['TIMSTAMP'],
                        'OK' if mccd.head.get('GOODTIME', True) else 'NOK'),
                    end='' if implot else '\n'
                )

                if not tzset:
                    # This is the first frame  which allows us to make
                    # some checks and initialisations.
                    tzero, read, gain, ok = initial_checks(mccd, rfile)

                    # Define the CCD processor function object
                    processor = ProcessCCDs(
                        rfile, read, gain, ccdproc, pool
                    )

                    # set flag to show we are set
                    if not ok:
                        break
                    tzset = True

                # De-bias the data. Retain a copy of the raw data as 'mccd'
                # in order to judge saturation. Processed data called 'pccd'
                if rfile.bias is not None:
                    # subtract bias
                    pccd = mccd - rfile.bias
                    bexpose = rfile.bias.head.get('EXPTIME',0.)
                else:
                    # no bias subtraction
                    pccd = mccd.copy()
                    bexpose = 0.

                if rfile.dark is not None:
                    # subtract dark, CCD by CCD
                    dexpose = rfile.dark.head['EXPTIME']
                    for cnam in pccd:
                        ccd = pccd[cnam]
                        cexpose = ccd.head['EXPTIME']
                        scale = (cexpose-bexpose)/dexpose
                        ccd -= scale*rfile.dark[cnam]

                if rfile.flat is not None:
                    # apply flat field to processed frame
                    pccd /= rfile.flat

                # Acummulate frames into processing groups for faster
                # parallelisation
                pccds.append(pccd)
                mccds.append(mccd)
                nframes.append(nframe)

                if len(pccds) == rfile['general']['ngroup']:
                    # parallel processing. This should usually be the first
                    # points at which it takes place
                    results = processor(pccds, mccds, nframes)

                    # write out results to the log file
                    alerts = logfile.write_results(results)

                    # print out any accumulated alert messages
                    if len(alerts):
                        print('\n'.join(alerts))

                    update_plots(
                        results, rfile, implot, lplot, imdev, lcdev,
                        pccds[-1], ccds, msub, nx, iset, plo, phi, 
                        ilo, ihi, xlo, xhi, ylo, yhi, tzero,
                        lpanel, xpanel, ypanel, tpanel, spanel, tkeep,
                        lbuffer, xbuffer, ybuffer, tbuffer, sbuffer
                    )

                    # Reset the frame buffers
                    pccds, mccds, nframes = [], [], []

        if len(mccds):
            # out of loop now. Finish processing any remaining
            # frames.
            results = processor(pccds, mccds, nframes)

            # write out results to the log file
            alerts = logfile.write_results(results)

            # print out any accumulated alert messages
            if len(alerts):
                print('\n'.join(alerts))

            update_plots(
                results, rfile, implot, lplot, imdev,
                lcdev, pccd, ccds, msub, nx, iset, plo, phi,
                ilo, ihi, xlo, xhi, ylo, yhi, tzero, lpanel,
                xpanel, ypanel, tpanel, spanel, tkeep,
                lbuffer, xbuffer, ybuffer, tbuffer, sbuffer
            )

            print('reduce finished')


###################################################################
#
# Stuff below is not exported outside this routine. Two routines of
# the same name but different action are located in psf_reduce

def ccdproc(cnam, ccds, rccds, nframes, read, gain, ccdwin, rfile, store):
    """Processing steps for a sequential set of images from the same
    CCD. This is designed for parallelising the processing across CCDs
    of multiarm cameras like ULTRACAM and HiPERCAM using
    multiprocessing. To be called *after* checking that any processing
    is needed.

    Arguments::

       cnam : string
          name of CCD, for information purposes (e.g. 'red', '3', etc)

       ccds : List of CCDs
          the CCDs for processing which should have been debiassed, flat
          fielded and multiplied by the gain to get into electrons.

       rccds : List of CCDs
          unprocessed CCDs, one-to-one correspondence with 'ccds', used
          to measure saturation

       nframes : List of ints
          frame numbers for each CCD

       read : CCD
          readnoise frame divided by the flatfield

       gain : CCD
          gain frame dmultiplied by the flatfield

       ccdwin : dict
          label of the Window enclosing each aperture

       rfile : Rfile object
          reduction control parameters. rfile.aper used to store the aperture
          parameters.

       store : dict
          dictionary of results

    Returns: (cnam, list[res]) where 'res' represents a tuple
    of results for each input CCD and contains the following:

    (nframe, store, ccdaper, results, mjdint, mjdfrac, mjdok, expose)

    """

    # At this point 'ccds' contains a list of CCD each of which
    # contains all the Windows of a CCD, 'ccdaper' all of its
    # apertures, 'ccdwin' the label of the Window enclosing each
    # aperture, 'rfile' contains control parameters, 'rflat' contains
    # the readout noise in electrons and divided by the flat as a CCD,
    # 'store' is a dictionary initially with jus 'mfwhm' and 'mbeta'
    # set = -1, but will pick up extra stuff from moveApers for use by
    # extractFlux along with revised values of mfwhm and mbeta which
    # are used to initialise profile fits next time.

    res = []
    for ccd, rccd, nframe in zip(ccds, rccds, nframes):
        # Loop through the CCDs supplied

        # move the apertures
        moveApers(cnam, ccd, read, gain, ccdwin, rfile, store)

        # extract flux from all apertures of each CCD. Return with the CCD
        # name, the store dictionary, ccdaper and then the results from
        # extractFlux for compatibility with multiprocessing. Note
        results = extractFlux(
            cnam, ccd, rccd, read, gain, ccdwin, rfile, store
        )

        # Save the essentials
        res.append((
            nframe, store, rfile.aper[cnam], results, ccd.head['MJDINT'],
            ccd.head['MJDFRAC'], ccd.head.get('GOODTIME',True),
            ccd.head.get('EXPTIME',1.)
        ))

    return (cnam, res)

def extractFlux(cnam, ccd, rccd, read, gain, ccdwin, rfile, store):
    """This extracts the flux of all apertures of a given CCD.

    The steps are (1) aperture resizing, (2) sky background estimation, (3)
    flux extraction. The apertures are assumed to be correctly positioned.

    It returns the results as a dictionary keyed on the aperture label. Each
    entry returns a list:

    [x, ex, y, ey, fwhm, efwhm, beta, ebeta, counts, countse, sky, esky,
    nsky, nrej, flag]

    flag = bitmask. See hipercam.core to see all the options which are
    referred to by name in the code e.g. ALL_OK. The various flags can
    signal that there no sky pixels (NO_SKY), the sky aperture was off
    the edge of the window (SKY_AT_EDGE), etc.

    This code::

       >> bset = flag & TARGET_SATURATED

    determines whether the data saturation flag is set for example.

    Arguments::

       cnam : string
          CCD identifier label

       ccd : CCD
           the debiassed, flat-fielded CCD.

       rccd : CCD
          corresponding raw CCD, used to work out whether data are
          saturated in target aperture.

       read : CCD
           readnoise divided by the flat-field

       gain : CCD
           gain multiplied by the flat field

       ccdwin : dictionary of strings
           the Window label corresponding to each Aperture

       rfile : Rfile
           reduce file configuration parameters

       store : dict of dicts
           see moveApers for what this contains.

    """

    # initialise flag
    flag = hcam.ALL_OK

    ccdaper = rfile.aper[cnam]

    # get the control parameters
    resize, extype, r1fac, r1min, r1max, r2fac, r2min, r2max, \
        r3fac, r3min, r3max = rfile['extraction'][cnam]

    results = {}
    mfwhm = store['mfwhm']

    if resize == 'variable' or extype == 'optimal':

        if mfwhm <= 0:
            # return early here as there is nothing we can do.
            print(
                (' *** WARNING: CCD {:s}: no measured FWHM to re-size'
                 ' apertures or carry out optimal extraction; no'
                 ' extraction possible').format(cnam)
            )
            # set flag to indicate no FWHM
            flag = hcam.NO_FWHM

            for apnam, aper in ccdaper.items():
                info = store[apnam]
                results[apnam] = {
                    'x': aper.x, 'xe': info['xe'],
                    'y': aper.y, 'ye': info['ye'],
                    'fwhm': info['fwhm'], 'fwhme': info['fwhme'],
                    'beta': info['beta'], 'betae': info['betae'],
                    'counts': 0., 'countse': -1,
                    'sky': 0., 'skye': 0., 'nsky': 0, 'nrej': 0,
                    'flag': flag
                }
            return results

        else:

            # Re-size the apertures
            for aper in ccdaper.values():
                aper.rtarg = max(r1min, min(r1max, r1fac*mfwhm))
                aper.rsky1 = max(r2min, min(r2max, r2fac*mfwhm))
                aper.rsky2 = max(r3min, min(r3max, r3fac*mfwhm))

    elif resize == 'fixed':

        # just apply the max and min limits
        for aper in ccdaper.values():
            aper.rtarg = max(r1min, min(r1max, aper.rtarg))
            aper.rsky1 = max(r2min, min(r2max, aper.rsky1))
            aper.rsky2 = max(r3min, min(r3max, aper.rsky2))

    else:
        raise hcam.HipercamError(
            "CCD {:s}: 'variable' and 'fixed' are the only"
            " aperture resizing options".format(
                cnam)
        )

    # apertures have been positioned in moveApers and now re-sized. Finally
    # we can extract something.
    for apnam, aper in ccdaper.items():

        # initialise flag
        flag = hcam.ALL_OK

        # extract Windows relevant for this aperture
        wnam = ccdwin[apnam]

        wdata = ccd[wnam]
        wread = read[wnam]
        wgain = gain[wnam]
        wraw = rccd[wnam]

        # extract sub-windows that include all of the pixels that could
        # conceivably affect the aperture. We have to check that 'extra'
        # apertures do not go beyond rsky2 which would normally be expected to
        # be the default outer radius
        rmax = aper.rsky2
        for xoff, yoff in aper.extra:
            rmax = max(rmax, np.sqrt(xoff**2+yoff**2) + aper.rtarg)

        # this is the region of interest
        x1, x2, y1, y2 = (
            aper.x-aper.rsky2-wdata.xbin, aper.x+aper.rsky2+wdata.xbin,
            aper.y-aper.rsky2-wdata.ybin, aper.y+aper.rsky2+wdata.ybin
        )

        try:

            # extract sub-Windows
            swdata = wdata.window(x1, x2, y1, y2)
            swread = wread.window(x1, x2, y1, y2)
            swgain = wgain.window(x1, x2, y1, y2)
            swraw = wraw.window(x1, x2, y1, y2)

            # some checks for possible problems. bitmask flags will be set if
            # they are encountered.
            xlo, xhi, ylo, yhi = swdata.extent()
            if xlo > aper.x-aper.rsky2 or xhi < aper.x+aper.rsky2 or \
               ylo > aper.y-aper.rsky2 or yhi < aper.y+aper.rsky2:
                # the sky aperture overlaps the edge of the window
                flag |= hcam.SKY_AT_EDGE

            if xlo > aper.x-aper.rtarg or xhi < aper.x+aper.rtarg or \
               ylo > aper.y-aper.rtarg or yhi < aper.y+aper.rtarg:
                # the target aperture overlaps the edge of the window
                flag |= hcam.TARGET_AT_EDGE

            for xoff, yoff in aper.extra:
                rout = np.sqrt(xoff**2+yoff**2) + aper.rtarg
                if xlo > aper.x-rout or xhi < aper.x+rout or \
                   ylo > aper.y-rout or yhi < aper.y+rout:
                    # an extra target aperture overlaps the edge of the window
                    flag |= hcam.TARGET_AT_EDGE

            # compute X, Y arrays over the sub-window relative to the centre
            # of the aperture and the distance squared from the centre (Rsq)
            # to save a little effort.
            x = swdata.x(np.arange(swdata.nx))-aper.x
            y = swdata.y(np.arange(swdata.ny))-aper.y
            X, Y = np.meshgrid(x, y)
            Rsq = X**2 + Y**2

            # squared aperture radii for comparison
            R1sq, R2sq, R3sq = aper.rtarg**2, aper.rsky1**2, aper.rsky2**2

            # sky selection, accounting for masks and extra (which we assume
            # acts like a sky mask as well)
            sok = (Rsq > R2sq) & (Rsq < R3sq)
            for xoff, yoff, radius in aper.mask:
                sok &= (X-xoff)**2 + (Y-yoff)**2 > radius**2
            for xoff, yoff in aper.extra:
                sok &= (X-xoff)**2 + (Y-yoff)**2 > R1sq

            # sky data
            dsky = swdata.data[sok]

            if len(dsky):

                # we have some sky!

                if rfile['sky']['method'] == 'clipped':

                    # clipped mean. Take average, compute RMS,
                    # reject pixels > thresh*rms from the mean.
                    # repeat until no new pixels are rejected.

                    thresh = rfile['sky']['thresh']
                    ok = np.ones_like(dsky, dtype=bool)
                    nrej = 1
                    while nrej:
                        slevel = dsky[ok].mean()
                        srms = dsky[ok].std()
                        nold = len(dsky[ok])
                        ok = ok & (np.abs(dsky-slevel) < thresh*srms)
                        nrej = nold - len(dsky[ok])

                    nsky = len(dsky[ok])

                    # serror -- error in the sky estimate.
                    serror = srms/np.sqrt(nsky)

                else:

                    # 'median' goes with 'photon'
                    slevel = dsky.median()
                    nsky = len(dsky)
                    nrej = 0

                    # read*gain/flat and flat over sky region
                    dread = swread.data[sok]
                    dgain = swgain.data[sok]

                    serror = np.sqrt(
                        (dread**2 + np.max(0, dsky)/dgain).sum()/nsky**2
                    )

            else:
                # no sky. will still return the flux in the aperture but set
                # flag and the sky uncertainty to -1
                flag |= hcam.NO_SKY
                slevel = 0
                serror = -1
                nsky = 0
                nrej = 0

            # size of a pixel which is used to taper pixels as they approach
            # the edge of the aperture to reduce pixellation noise
            size = np.sqrt(wdata.xbin*wdata.ybin)

            # target selection, accounting for extra apertures and allowing
            # pixels to contribute if their centres are as far as size/2 beyond
            # the edge of the circle (but with a tapered weight)
            dok = Rsq < (aper.rtarg+size/2.)**2

            if not dok.any():
                # check there are some valid pixels
                flag |= hcam.NO_DATA
                raise hcam.HipercamError('no valid pixels in aperture')

            # check for saturation and nonlinearity
            if cnam in rfile.warn:
                if swraw.data[dok].max() >= rfile.warn[cnam]['saturation']:
                    flag |= hcam.TARGET_SATURATED

                if swraw.data[dok].max() >= rfile.warn[cnam]['nonlinear']:
                    flag |= hcam.TARGET_NONLINEAR

            else:
                warnings.warn(
                    'CCD {:s} has no nonlinearity or saturation levels set'
                )

            # Pixellation amelioration:
            #
            # The weight of a pixel is set to 1 at the most and then linearly
            # declines as it approaches the edge of the aperture. The scale over
            # which it declines is set by 'size', the geometric mean of the
            # binning factors. A pixel with its centre exactly on the edge
            # gets a weight of 0.5.
            wgt = np.minimum(
                1, np.maximum(
                    0, (aper.rtarg+size/2.-np.sqrt(Rsq))/size
                )
            )
            for xoff, yoff in aper.extra:
                rsq = (X-xoff)**2 + (Y-yoff)**2
                dok |= rsq < (aper.rtarg+size/2.)**2
                wg = np.minimum(
                    1, np.maximum(
                        0, (aper.rtarg+size/2.-np.sqrt(rsq))/size
                    )
                )
                wgt = np.maximum(wgt, wg)

            # the values needed to extract the flux.
            dtarg = swdata.data[dok]
            dread = swread.data[dok]
            dgain = swgain.data[dok]
            wtarg = wgt[dok]

            # 'override' to indicate we want to override the readout noise.
            if nsky and rfile['sky']['error'] == 'variance':
                # from sky variance
                rd = srms
                override = True
            else:
                rd = dread
                override = False

            # count above sky
            diff = dtarg - slevel

            if extype == 'normal' or extype == 'optimal':

                if extype == 'optimal':
                    # optimal extraction. Need the profile
                    warnings.warn(
                        'Transmission plot is not reliable'
                        ' with optimal extraction'
                    )

                    mbeta = store['mbeta']
                    if mbeta > 0.:
                        prof = fitting.moffat(
                            X[dok], Y[dok], 0., 1., 0., 0., mfwhm, mbeta,
                            wdata.xbin, wdata.ybin,
                            rfile['apertures']['fit_ndiv']
                        )
                    else:
                        prof = fitting.gaussian(
                            X[dok], Y[dok], 0., 1., 0., 0., mfwhm,
                            wdata.xbin, wdata.ybin,
                            rfile['apertures']['fit_ndiv']
                        )

                    # multiply weights by the profile
                    wtarg *= prof

                # now extract
                counts = (wtarg*diff).sum()

                if override:
                    # in this case, the "readout noise" includes the component
                    # due to the sky background so we use the sky-subtracted
                    # counts above sky for the object contribution.
                    var = (wtarg**2*(rd**2 + np.maximum(0, diff)/dgain)).sum()
                else:
                    # in this case we are using the true readout noise and we
                    # just use the data (which should be debiassed) without
                    # removal of the sky.
                    var = (wtarg**2*(rd**2 + np.maximum(0, dtarg)/dgain)).sum()

                if serror > 0:
                    # add in factor due to uncertainty in sky estimate
                    var += (wtarg.sum()*serror)**2

                countse = np.sqrt(var)

            else:
                raise hcam.HipercamError(
                    'extraction type = {:s} not recognised'.format(extype)
                )

            info = store[apnam]

            results[apnam] = {
                'x': aper.x, 'xe': info['xe'],
                'y': aper.y, 'ye': info['ye'],
                'fwhm': info['fwhm'], 'fwhme': info['fwhme'],
                'beta': info['beta'], 'betae': info['betae'],
                'counts': counts, 'countse': countse,
                'sky': slevel, 'skye': serror, 'nsky': nsky,
                'nrej': nrej, 'flag': flag
            }

        except hcam.HipercamError as err:

            info = store[apnam]
            flag |= hcam.NO_EXTRACTION

            results[apnam] = {
                'x': aper.x, 'xe': info['xe'],
                'y': aper.y, 'ye': info['ye'],
                'fwhm': info['fwhm'], 'fwhme': info['fwhme'],
                'beta': info['beta'], 'betae': info['betae'],
                'counts': 0., 'countse': -1,
                'sky': 0., 'skye': 0., 'nsky': 0, 'nrej': 0,
                'flag': flag
            }

    # finally, we are done
    return results
