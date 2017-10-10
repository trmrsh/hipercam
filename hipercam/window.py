# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""
Defines classes to represent sub-windows of a CCD and associated
functions.
"""

import warnings
import json
import math
from collections import OrderedDict

import numpy as np
from numpy.lib.stride_tricks import as_strided

from astropy.io import fits
from astropy.convolution import Gaussian2DKernel, convolve, convolve_fft
from astropy.modeling import models, fitting, Fittable2DModel, Parameter

import matplotlib.pyplot as plt
from .core import *
from .group import *
 
__all__ = (
    'Window', 'Windat',
    'CcdWin', 'MccdWin',
    'fitGaussian', 'fitMoffat',
    'combFit'
)

class Window:
    """
    Class representing a window of a CCD. This represents an arbitrary
    rectangular region of binned pixels. The lower-left pixel of the CCD
    is assumed to have coordinates (x,y) = (1,1). :class:`Window` dimensions are
    in binned pixels.

        >>> from hipercam import Window
        >>> win = Window(12, 6, 100, 150, 2, 3)
        >>> print(win)
        Window(12, 6, 100, 150, 2, 3)

    """

    def __init__(self, llx, lly, nx, ny, xbin, ybin):
        """
        Constructor. Arguments::

        llx : (int)
            X position of lower-left pixel of window (unbinned pixels)

        lly : (int)
            Y position of lower-left pixel of window (unbinned pixels)

        nx : (int)
            X dimension of window, binned pixels

        ny : (int)
            Y dimension of window, binned pixels

        xbin : (int)
            Binning factor in X

        ybin : (int)
            Binning factor in Y
        """

        self.llx = llx
        self.lly = lly
        self.xbin = xbin
        self.ybin = ybin
        # Have to take care with the next two since in
        # Windat they are connected to the array size
        self._nx = nx
        self._ny = ny

    @property
    def nx(self):
        """
        Returns binned X-dimension of the :class:`Window`. 
        """
        return self._nx

    @nx.setter
    def nx(self, nx):
        if nx < 1:
            raise ValueError(
                'hipercam.Window.nx: nx = {:d} is invalid'.format(nx))
        self._nx = nx

    @property
    def ny(self):
        """
        Returns binned Y-dimension of the :class:`Window`. 
        """
        return self._ny

    @ny.setter
    def ny(self, ny):
        if ny < 1:
            raise ValueError(
                'hipercam.Window.ny: ny = {:d} is invalid'.format(ny))
        self._ny = ny

    def __repr__(self):
        return 'Window(llx={!r}, lly={!r}, nx={!r}, ny={!r}, xbin={!r}, ybin={!r})'.format(self.llx, self.lly, self.nx, self.ny, self.xbin, self.ybin)

    def format(self):
        """Used to ensure that only the Window format gets printed which is
        useful in some instances. Relying on __repr__ carries the risk of
        being overloaded."""

        return 'Window(llx={!r}, lly={!r}, nx={!r}, ny={!r}, xbin={!r}, ybin={!r})'.format(self.llx, self.lly, self.nx, self.ny, self.xbin, self.ybin)

    @property
    def urx(self):
        """
        Returns (unbinned) X pixel at upper-right of :class:`Window`
        """
        return self.llx-1+self.nx*self.xbin

    @property
    def ury(self):
        """
        Returns (unbinned) Y pixel at upper-right of :class:`Window`
        """
        return self.lly-1+self.ny*self.ybin

    @property
    def xlo(self):
        """
        Returns left-hand edge of window (llx-0.5)
        """
        return self.llx-0.5

    @property
    def xhi(self):
        """
        Returns right-hand edge of window (urx+0.5)
        """
        return self.llx-1+self.nx*self.xbin+0.5

    @property
    def ylo(self):
        """
        Returns bottom edge of window (lly-0.5)
        """
        return self.lly-0.5

    @property
    def yhi(self):
        """
        Returns top edge of window (ury+0.5)
        """
        return self.lly-1+self.ny*self.ybin+0.5

    def x(self, xpix):
        """Given an X-pixel position, returns the physical X in the CCD.

        Arguments::

          xpix : (float / ndarray)
            X-pixel position in terms of binned pixels. Centre of left-most
            pixel is 0.

        Returns the physical location measured in unbinned pixels, with the
        centre of left-most pixels of the CCD = 1.0
        """
        return self.llx + self.xbin*(xpix+0.5) - 0.5

    def y(self, ypix):
        """Given a Y-pixel position, returns the physical Y in the CCD.

        Arguments::

          ypix : (float / ndarray)
            Y-pixel position in terms of binned pixels. Centre of lowest
            pixel is 0.

        Returns the physical location measured in unbinned pixels, with the
        centre of lowest pixels of the CCD = 1.0
        """
        return self.lly + self.ybin*(ypix+0.5) - 0.5

    def x_pixel(self, x):
        """The inverse of `x`: returns the X-pixel position given a physical
        X location.

        Arguments::

          x : (float)
            the physical location measured in unbinned pixels, with the centre
            of left-most pixels of the CCD = 1.0

        Returns the X-pixel position in terms of binned pixels. Centre of the
        left-most pixel is 0.

        """
        return (x+0.5-self.llx)/self.xbin-0.5

    def y_pixel(self, y):
        """The inverse of `y`: returns the Y-pixel position given a physical
        Y location.

        Arguments::

          Y : (float)
            the physical location measured in unbinned pixels, with the centre
            of lowest pixels of the CCD = 1.0

        Returns the Y-pixel position in terms of binned pixels. Centre of the
        lowest pixel is 0.

        """
        return (y+0.5-self.lly)/self.ybin-0.5

    def extent(self):

        """
        Returns (left,right,bottom,top) boundaries of :class:`Window`
        i.e. (xlo,xhi,ylo,yhi)
        """
        return (self.xlo,self.xhi,self.ylo,self.yhi)

    def outside(self, win):
        """
        Returns True if `self` contains the :class:Window `win` in such a way
        that it could be cut down to it. This implies that even if binned, its
        pixels are "in step", aka "synchronised", and that its binning factors
        are integer divisors of those of `win`.

        Arguments::

          win  : (:class:Window)
             the :class:Window that we are testing against to see if `self` surrounds it.

        See also :func:`inside`, :func:`window`
        """
        return win.xbin % self.xbin == 0 and win.ybin % self.ybin == 0 and \
            self.llx <= win.llx and self.urx >= win.urx and \
            self.lly <= win.lly and self.ury >= win.ury and \
            (win.llx-self.llx) % self.xbin == 0 and \
            (win.lly-self.lly) % self.ybin == 0

    def inside(self, win):
        """
        Returns True if `win` contains `self` in such a way that it could be
        cut down to it. This implies that even if binned, its pixels are "in
        step", aka "synchronised", and that its bin factors are integer
        divisors of those of `self`.

        Arguments::

          win  : (:class:Window)
             the :class:Window that we are testing against to see if `self` in inside it.

        See also :func:`outside`, :func:`window`
        """
        return self.xbin % win.xbin == 0 and self.ybin % win.ybin == 0 and \
            win.llx <= self.llx and win.urx >= self.urx and \
            win.lly <= self.lly and win.ury >= self.ury and \
            (self.llx-win.llx) % win.xbin == 0 and \
            (self.lly-win.lly) % win.ybin == 0

    def clash(self, win):
        """Raises a ValueError if two :class: `Window`s are considered to 'clash'.  In
        this case this means if they have any pixels in common.  This method
        is used when :class: `Window`s are collected into :class:`Group`s

        """
        if self.llx <=  win.urx and self.urx >= win.llx and \
           self.lly <=  win.ury and self.ury >= win.lly:
            raise ValueError(
                'self = {:s} clashes with win = {:s}'.format(self.format(), win.format())
                )

    def xy(self):
        """Returns two 2D arrays containing the x and y values at the centre
        of each pixel defined by the :class:`Window`. See numpy.meshgrid to
        see what this means.
        """
        # generate 1D x and y arrays along the edges
        x = np.linspace(self.x(0),self.x(self.nx-1),self.nx)
        y = np.linspace(self.y(0),self.y(self.ny-1),self.ny)

        return np.meshgrid(x,y)

    def matches(self, win):
        """Tests that the :class:`Window` matches another. If all OK, returns None,
        otherwise raises a ValueError reporting the two :class:`Window`s. See
        also `__eq__` and `clash`

        """
        if self != win:
            raise ValueError(
                'hipercam.Window.matches: self = {!s} clashes with win = {!s}'.format(self,win)
            )

    def copy(self, memo=None):
        """Returns a copy (deepcopy) of the :class:`Window`

        copy.copy and copy.deepcopy of a `Window` use this method
        """
        return Window(self.llx, self.lly, self.nx, self.ny, self.xbin, self.ybin)

    def distance(self, x, y):
        """Calculates the minimum distance of a point from the edge of the Window. If
        the point is outside the Window the distance will be negative; if
        inside it will be positive. The edge is defined as the line running
        around the outside of the outer set of pixels. For a point outside the
        box in both x and y, the value returned is a lower limit to the
        distance.

        """
        if x < self.xlo:
            if y < self.ylo:
                dist = -min(self.xlo-x, self.ylo-y)
            elif y > self.yhi:
                dist = -min(self.xlo-x, y-self.yhi)
            else:
                dist = x-self.xlo

        elif x > self.xhi:
            if y < self.ylo:
                dist = -min(x-self.xhi, self.ylo-y)
            elif y > self.yhi:
                dist = -min(x-self.xhi, y-self.yhi)
            else:
                dist = self.xhi-x

        else:
            if y < self.ylo:
                dist = y-self.ylo
            elif y > self.yhi:
                dist = self.yhi-y
            else:
                # we are *in* the box
                dist = min(x-self.xlo, self.xhi-x, y-self.ylo, self.yhi-y)

        return dist

    def window(self, xlo, xhi, ylo, yhi):
        """Generates a new Window by windowing it to match the
        complete pixels visible within the range xlo to xhi, ylo to yhi.

        Arguments::

           xlo : (float)
              minimum X, unbinned pixels (extreme left pixels of CCD centred
              on 1)

           xhi : (float)
              maximum X, unbinned pixels

           ylo : (float)
              minimum Y, unbinned pixels (bottom pixels of CCD centred on 1)

           yhi : (float)
              maximum Y, unbinned pixels

        Returns the windowed Window. Raises a ValueError if there are no
        visible pixels.
        """

        llx = max(self.llx, self.llx + self.xbin*int(math.ceil((xlo-self.xlo)/self.xbin)))
        lly = max(self.lly, self.lly + self.ybin*int(math.ceil((ylo-self.ylo)/self.ybin)))
        nx = self.nx - (llx-self.llx)//self.xbin - max(0,int(math.ceil((self.xhi-xhi)/self.xbin)))
        ny = self.ny - (lly-self.lly)//self.ybin - max(0,int(math.ceil((self.yhi-yhi)/self.ybin)))

        if nx <= 0 or ny <= 0:
            raise ValueError(
                '{!r} has no overlap with region = ({:.2f},{:.2f},{:.2f},{:.2f})'.format(
                    self, xlo, xhi, ylo, yhi)
                )

        return Window(llx, lly, nx, ny, self.xbin, self.ybin)

    def __copy__(self):
        return self.copy()

    def __deepcopy__(self, memo):
        return self.copy(memo)

    def __eq__(self, win):
        """
        Defines equality. Two :class:`Window`s are equal if they match exactly
        (same lower left corner, dimensions and binning factors)
        """
        return self.llx == win.llx and  self.lly == win.lly and \
            self.nx == win.nx and self.ny == win.ny and \
            self.xbin == win.xbin and self.ybin == win.ybin

    def __ne__(self, win):
        """Defines equality. Two :class:`Window`s are equal if they match
        exactly (same lower left corner, dimensions and binning factors)

        """
        return not (self == win)

    def toJson(self, fname):
        """Dumps Window in JSON format to fp"""
        with open(fname,'w') as fp:
            json.dump(self, fp, cls=_Encoder, indent=2)

    @classmethod
    def fromJson(cls, fname):
        """Read from JSON-format file fname"""
        with open(fname) as fp:
            win = json.load(fp, cls=_Decoder)
        return win

class _Encoder (json.JSONEncoder):
    """
    Provides a default that can be used to write Window
    objects to json files
    """
    def default(self, obj):
        if isinstance(obj, Window):
            return OrderedDict(
                (
                    ('Comment', 'hipercam.Window'),
                    ('llx', obj.llx),
                    ('lly', obj.lly),
                    ('nx', obj.nx),
                    ('ny', obj.ny),
                    ('xbin', obj.xbin),
                    ('ybin', obj.ybin)
                ))

        super().default(obj)

class _Decoder(json.JSONDecoder):

    def __init__(self, *args, **kwargs):
        super().__init__(object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, obj):
        # looks out for Aperture objects. Everything else done by default
        if 'Comment' in obj and r['Comment'] == 'hipercam.Window':
            return Window(
                obj['llx'], obj['lly'], obj['nx'], obj['ny'],
                obj['xbin'], obj['ybin']
            )

        return obj

class CcdWin(Group):
    """Class representing all the :class:`Window`s for a single CCD.
    """

    def __init__(self, wins=Group(Window)):
        """Constructs a :class:`CcdWin`.

        Arguments::

          wins : (Group)
              Group of :class:`Window` objects
        """
        super().__init__(Window, wins)

    def __repr__(self):
        return '{:s}(wins={:s})'.format(
            self.__class__.__name__, super().__repr__()
            )

    def toJson(self, fname):
        """Dumps ccdWin in JSON format to a file

        Arguments::

           fname : (string)
              name of file to dump to
        """

        # dumps as list to retain order through default iterator encoding that
        # buggers things otherwise
        listify = ['hipercam.CcdWin'] + list(self.items)
        with open(fname,'w') as fp:
            json.dump(listify, fp, cls=_Encoder, indent=2)

class MccdWin(Group):
    """Class representing all the :class:`Window`s for multiple CCDs.
    """

    def __init__(self, wins=Group(CcdWin)):
        """Constructs a :class:`MccdWin`.

        Arguments::

          aps : (Group)
              Group of :class:`CcdWin` objects
        """
        super().__init__(CcdWin, wins)

    def __repr__(self):
        return '{:s}(wins={:s})'.format(
            self.__class__.__name__, super().__repr__()
            )

    def toJson(self, fname):
        """Dumps MccdWin in JSON format to a file

        Arguments::

            fname : (string)
               file to dump to
        """
        # dumps as list to retain order through default iterator encoding
        # that buggers things otherwise
        listify = ['hipercam.MccdWin'] + list(
            ((key,['hipercam.CcdWin']+list(val.items())) \
             for key, val in self.items())
        )
        with open(fname, 'w') as fp:
            json.dump(listify, fp, cls=_Encoder, indent=2)

    @classmethod
    def fromJson(cls, fname):
        """Read from JSON-format file fname

        Returns an MccdWin object.
        """
        with open(fname) as fp:
            obj = json.load(fp, cls=_Decoder)
        listify = [(v1,CcdWin(v2[1:])) for v1,v2 in obj[1:]]
        mccdwin = MccdWin(listify)
        return mccdwin

    @classmethod
    def fromMccd(cls, mccd):
        mccdwin = MccdWin()
        for cnam, ccd in mccd.items():
            mccdwin[cnam] = CcdWin()
            for wnam, wind in ccd.items():
                mccdwin[cnam][wnam] = wind.win
        return mccdwin

class Windat(Window):
    """Class representing a CCD window with data. Constructed from
    a :class:`Window` and a :class:`numpy.ndarray` which is stored
    in an attribute called `data`.

        >>> import numpy as np
        >>> from hipercam import Window, Windat
        >>> win = Window(12, 6, 100, 150, 2, 3)
        >>> data = np.ones((150,100))
        >>> wind = Windat(win,data)
        >>> wind += 0.5
        >>> wind *= 2

    You cannot directly change the nx, ny values of a Windat; you have
    to change its data array attribute and nx and ny will be taken from it.

    :class:`Windat` objects support various arithematical operations such as
    subtraction or additoin of constants. The end result of these always has a
    float type for safety to avoid problems with e.g. trying to make the
    result of adding a float to an integer an integer or with the range of
    integers.

    """

    def __init__(self, win, data=None):
        """
        Constructs a :class:`Windat`

        Arguments::

          win : (Window)
              the Window

          data : (numpy.ndarray)
              the data (2D). The dimension must match those in win unless
              data is None in which case a zero array of the correct size
              will be created. A ValueError will be raised if not.
        """
        if data is None:
            self.data = np.zeros((win.ny,win.nx))
        else:
            # Run a couple of checks
            if data.ndim != 2:
                raise ValueError(
                    'Windat.__init__: data must be 2D. Found {0:d}'.format(data.ndim))
            ny, nx = data.shape
            if nx != win.nx or ny != win.ny:
                raise ValueError(
                    'Windat.__init__: win vs data dimension conflict. NX: {0:d} vs {1:d}, NY: {2:d} vs {3:d}'.format(win.nx,nx,win.ny,ny))

            self.data = data

        super().__init__(win.llx, win.lly, win.nx, win.ny, win.xbin, win.ybin)

    @property
    def nx(self):
        """
        Returns binned X-dimension of the :class:`Windat`. 
        """
        return self.data.shape[1]

    @nx.setter
    def nx(self, nx):
        raise NotImplementedError('hipercam.Windat.nx: cannot set nx directly; change data array instead')

    @property
    def ny(self):
        """
        Returns binned Y-dimension of the :class:`Windat`. 
        """
        return self.data.shape[0]

    @ny.setter
    def ny(self, ny):
        raise NotImplementedError('hipercam.Windat.ny: cannot set ny directly; change data array instead')

    @classmethod
    def rhdu(cls, hdu):
        """Constructs a :class:`Windat` from an ImageHdu. Requires
        header parameters 'LLX', 'LLY', 'XBIN' and 'YBIN' to be defined.
        """
        head = hdu.header
        data = hdu.data

        # construct Window
        llx = head['LLX']
        lly = head['LLY']
        xbin = head['XBIN']
        ybin = head['YBIN']
        ny, nx = data.shape
        win = Window(llx, lly, nx, ny, xbin, ybin)

        return cls(win, data)

    @property
    def size(self):
        """
        Number of pixels
        """
        return self.data.size

    @property
    def win(self):
        """A copy of the :class:`Window` underlying the :class:`Windat`"""
        return super().copy()

    def set_const(self, val):
        """Sets the data array to a constant"""
        self.data[:] = val

    def add_noise(self, readout, gain):
        """Adds noise to a :class:`Windat` according to a variance
        calculated from V = readout**2 + counts/gain.
        Arguments::

          readout : (float)
              RMS readout noise in counts

          gain : (float)
              Gain in electrons per count.
        """
        sig = np.sqrt(readout**2+self.data/gain)
        self.data += np.random.normal(scale=sig)

    def min(self):
        """
        Returns the minimum value of the :class:`Windat`.
        """
        return self.data.min()

    def max(self):
        """
        Returns the maximum value of the :class:`Windat`.
        """
        return self.data.max()

    def mean(self):
        """
        Returns the mean value of the :class:`Windat`.
        """
        return self.data.mean()

    def median(self):
        """
        Returns the median value of the :class:`Windat`.
        """
        return np.median(self.data)

    def sum(self):
        """
        Returns the sum of the :class:`Windat`.
        """
        return self.data.sum()

    def std(self):
        """
        Returns the standard deviation of the :class:`Windat`.
        """
        return self.data.std()

    def percentile(self, q):
        """
        Computes percentile(s) of a :class:`Windat`.

        Arguments::

        q : (float or sequence of floats)
          Percentile(s) to use, in range [0,100]
        """
        return np.percentile(self.data, q)

    def whdu(self, fhead=None):
        """Writes the :class:`Windat` to an :class:`astropy.io.fits.ImageHDU` with
        extension name 'WIND'

        Arguments::

          fhead : (astropy.io.fits.Header)
             A FITS header object for passing in meta data. The location of
             the lower-left pixel and the binning factors will be added to it,
             so it will be modified on exit. If None on entry, it will be
             created.

        Returns::

          hdu : (astropy.io.fits.ImageHDU)
             The HDU containg the data and the header. It will have extension WIND.

        """

        if fhead is None:
            fhead = fits.Header()

        fhead['LLX'] = (self.llx,  'X-coordinate of lower-left pixel')
        fhead['LLY'] = (self.lly,  'Y-coordinate of lower-left pixel')
        fhead['XBIN'] = (self.xbin, 'Binning factor in X')
        fhead['YBIN'] = (self.ybin, 'Binning factor in Y')

        return fits.ImageHDU(self.data, fhead, name='WIND')

    def add_fxy(self, funcs, ndiv=0):
        """Routine to add in the results of evaluating a function or a list of
        functions of x & y to the :class:`Windat`.  Each function must take 2D
        arrays of x and y values for each pixel of the :class:`Windat` and
        return an array of values for each point. If you have lots of things
        to add, this routine saves some overhead by only generating the x,y
        pixel arrays once at the start. Pixels can be subdivided ndiv per
        unbinned pixel (ndiv == 0 simply computes the result at pixel centre)

        Arguments::

          funcs : (a callable or a list of callables)
              the callable(s) must have signature::

                 arr = func(x,y)

              where x and y are 2D arrays containing the x and y values at the
              centre of each pixel in the :class:`Windat`. Each func must have
              a method 'offset(self, dx, dy)' which returns a copy of the
              function displaced in centre by dx, dy unbinned pixels.

          ndiv : (int)

              a sub-division factor used to improve the photometric accuracy
              when pixellation matters. The funcs are computed over a grid of
              ndiv*ndiv points per unbinned pixel.

        """

        # generate X,Y arrays
        x,y = self.xy()
        if ndiv:
            scale = 1/ndiv**2/self.xbin/self.ybin
        try:
            for func in funcs:
                if ndiv:
                    for iy in range(self.ybin*ndiv):
                        dy = (iy - (ndiv - 1) / 2) / ndiv
                        for ix in range(self.xbin*ndiv):
                            dx = (ix - (ndiv - 1) / 2) / ndiv
                            ofunc = func.offset(dx,dy)
                            ofunc(x,y,self.data,scale)
                else:
                    func(x,y,self.data)

        except TypeError:
            # If funcs is not iterable, assume it is just one callable
            if ndiv:
                for iy in range(self.ybin*ndiv):
                    dy = (iy - (ndiv - 1) / 2) / ndiv
                    for ix in range(self.xbin*ndiv):
                        dx = (ix - (ndiv - 1) / 2) / ndiv
                        ofunc = funcs.offset(dx,dy)
                        ofunc(x,y,self.data,scale)
            else:
                funcs(x,y,self.data)

    def copy(self, memo=None):
        """Returns a copy (deepcopy) of the :class:`Windat`

        copy.copy and copy.deepcopy of a `Windat` use this method
        """
        return Windat(self.win, self.data.copy())

    def window(self, xlo, xhi, ylo, yhi):
        """Creates a new Windat by windowing it down to whatever complete pixels are
        visible in the region xlo to xhi, ylo to yhi.

        Arguments::

           xlo : (float)
              minimum X, unbinned pixels (extreme left pixels of CCD centred on 1)

           xhi : (float)
              maximum X, unbinned pixels

           ylo : (float)
              minimum Y, unbinned pixels (bottom pixels of CCD centred on 1)

           yhi : (float)
              maximum Y, unbinned pixels

        Returns the windowed Windat.
        """
        win = super().window(xlo, xhi, ylo, yhi)

        # we know the Window generated is in step with the current Window which saves
        # some checks that would be applied if 'crop' was used at this point
        x1 = (win.llx-self.llx)//self.xbin
        y1 = (win.lly-self.lly)//self.ybin
        if self.data is None:
            return Windat(win)
        else:
            return Windat(win, self.data[y1:y1+win.ny, x1:x1+win.nx])

    def crop(self, win):
        """Creates a new :class:Windat by cropping the current :class:Windat to the
        format defined by :class:Window `win`. Will throw a ValueError if the
        operation is impossible or results in no overlap. The current
        :class:Windat must lie outside `win` and must be synchronised (in
        step) if any binning is used. If binning is used then the binning
        factors of the :class:Windat must be divisors of the binning factors
        of those of `win`. If binning takes place it will be carried out by
        averaging, as appropriate for cropping flat-field frames (but not star
        fields).

        Arguments::

           win : (:class:Window)
              the new format to apply.

        """
        if self.outside(win):
            # first slice down to size
            xstart = (win.llx-self.llx)//self.xbin
            xend = xstart + win.nx*win.xbin//self.xbin
            ystart = (win.lly-self.lly)//self.ybin
            yend = ystart + win.ny*win.ybin//self.ybin
            data = self.data[ystart:yend,xstart:xend]

            if win.xbin > self.xbin or win.ybin > self.ybin:
                # in this case we also need to rebin which is a matter of
                # averaging over blocks of (win.xbin//self.xbin,
                # win.ybin//self.ybin) which we do with a striding trick
                block = (win.ybin//self.ybin, win.xbin//self.xbin)
                shape= (data.shape[0]//block[0], data.shape[1]//block[1]) + block
                strides= (block[0]*data.strides[0], block[1]*data.strides[1]) + data.strides
                data = as_strided(data, shape, strides)
                data = data.mean(-1).mean(-1)

            return Windat(win, data)

        else:
            raise ValueError(
                'Cannot crop {!r} to {!r}'.format(self,win)
            )

    def float32(self):
        """
        Converts the data type of the array to float32 if and only
        if it is currently float64. Leaves all other types untouched.
        This is to save space on output. Note that because of numpy's
        promotion to higher precision, there is little point trying to
        use this to save internal memory.
        """
        if self.data.dtype == np.float64:
            self.data = self.data.astype(np.float32)

    def uint16(self):
        """
        Converts the data type of the array to uint16. A warning will be issued
        if there will be loss of precision. A ValueError is thrown if any data
        are outside the range 0 to 65535 This is to save space on output.
        """
        if self.data.dtype != np.uint16:
            if np.any((self.data < 0) | (self.data > 65535)):
                raise ValueError('data outside range 0 to 65535')

            if not np.equal(np.mod(self.data, 1), 0):
                warnings.warn('conversion to uint16 will result in loss of precision')

            self.data = self.data.astype(np.uint16)

    def find(self, fwhm, fft=True):
        """
        Finds the position of one star in a :class:Windat. Works by convolving
        the image with a gaussian of FWHM = fwhm, and returns the location of
        the brightest pixel of the result along with the value of that pixel
        in the uncolvolved image. The convolution improves the reliability of
        the identification of the object position and reduces the chance of
        problems being caused by cosmic rays, although if there is more
        overall flux in a cosmic ray than the star, it will go wrong, so some
        form of prior cleaning is advisable in such cases. It is up to the
        user to make the :class:Windat small enough the star of interest is the
        brightest object (see e.g. `window`).

        This routine is intended to provide a first cut in position for more
        precise methods to polish.

        Arguments::

          fwhm  : (float)
            Gaussian FWHM in pixels. If <= 0, there will be no convolution, although
            this is not advisable as a useful strategy.

          fft   : (bool)
            The astropy.convolution routines are used. By default FFT-based
            convolution is applied as it scales better with fwhm, especially
            for fwhm >> 1, however the direct method (fft=False) may be faster
            for small fwhm values and images and has a better behaviour at the
            edges where it extends value with the nearest pixel while the FFT
            wraps values.

        Returns:: 

          (x,y,peak) : (tuple) x,y is the location of the brightest pixel
            measured in terms of CCD coordinates (i.e. lower-left pixel is at
            (1,1)). `peak` is the image value at the peak pixel, in the
            *unconvolved* image. It might be useful for initial estimates of
            peak height.

        """
        if fwhm > 0:
            kern = Gaussian2DKernel(fwhm/np.sqrt(8*np.log(2)))
            if fft:
                cimg = convolve_fft(self.data, kern, 'wrap')
            else:
                cimg = convolve(self.data, kern, 'extend')
        else:
            cimg = self.data

        # locate coords of maximum pixel.
        iy, ix = np.unravel_index(cimg.argmax(), cimg.shape)

        # return with device coords and the value
        return (self.x(ix),self.y(iy),self.data[iy,ix])

    def __copy__(self):
        return self.copy()

    def __deepcopy__(self, memo):
        return self.copy(memo)

    def __repr__(self):
        return 'Windat(win={:s}, data={!r})'.format(
            super().__repr__(), self.data
        )

    # lots of arithematic routines

    def __iadd__(self, other):
        """Adds `other` to the :class:`Windat` as 'wind += other'. `other` can be
        another :class:`Windat`, or any object that can be added to a :class:`numpy.ndarray`.

        For safety, the data array of the result will have a float type.
        """
        if isinstance(other, Windat):
            # test compatibility between the windows (raises an exception)
            self.matches(other)
            num = other.data
        else:
            num = other

        # carry out operation 'in place' or with copying,
        # depending on compatibility of the array type
        if np.can_cast(np.float, self.data.dtype):
            self.data += num
        else:
            self.data = np.add(self.data, num, dtype=np.float)

        return self

    def __isub__(self, other):
        """Subtracts `other` from the :class:`Windat` as 'wind -= other`. `other` can
        be another Windat, or any object that can be subtracted from a
        :class:`numpy.ndarray`.

        For safety, the data array of the result will have a float type.
        """
        if isinstance(other, Windat):
            # test compatibility between the windows (raises an exception)
            self.matches(other)
            num = other.data
        else:
            num = other

        # carry out operation 'in place' or with copying,
        # depending on compatibility of the array type
        if np.can_cast(np.float, self.data.dtype):
            self.data -= num
        else:
            self.data = np.subtract(self.data, num, dtype=np.float)

        return self

    def __imul__(self, other):
        """Multiplies the :class:`Windat` by `other` as 'wind *= other'. `other` can be
        another :class:`Windat`, or any object that can multiply a :class:`numpy.ndarray`.

        For safety, the data array of the result will have a float type.
        """
        if isinstance(other, Windat):
            # test compatibility between the windows (raises an exception)
            self.matches(other)
            num = other.data
        else:
            num = other

        # carry out operation 'in place' or with copying,
        # depending on compatibility of the array type
        if np.can_cast(np.float, self.data.dtype):
            self.data *= num
        else:
            self.data = np.multiply(self.data, num, dtype=np.float)

        return self

    def __itruediv__(self, other):
        """Divides `other` into the :class:`Windat` as 'wind /= other`. `other` can be
        another Windat, or any object that can be divided into a
        :class:`numpy.ndarray`.

        For safety, the data array of the result will have a float type.
        """
        if isinstance(other, Windat):
            # test compatibility between the windows (raises an exception)
            self.matches(other)
            num = other.data
        else:
            num = other

        # carry out operation 'in place' or with copying,
        # depending on compatibility of the array type
        if np.can_cast(np.float, self.data.dtype):
            self.data /= num
        else:
            self.data = np.true_divide(self.data, num, dtype=np.float)

        return self

    def __add__(self, other):
        """Adds `other` to a :class:`Windat` as `wind + other`.  Here `other` can be a
        compatible :class:`Windat` (identical window) or any object that can
        be added to a :class:`numpy.ndarray`, e.g. a float, another matching
        array, etc. 

        For safety, the data array of the result will have a float type.
        """
        if isinstance(other, Windat):
            # test compatibility between the windows (raises an exception)
            self.matches(other)
            num = other.data
        else:
            num = other

        # carry out addition to a float type
        data = np.add(self.data, num, dtype=np.float)
        return Windat(self.win, data)


    def __radd__(self, other):
        """Adds `other` to a :class:`Windat` as `other + wind`.  Here `other` is any
        object that can be added to a :class:`numpy.ndarray`, e.g. a float,
        another matching array, etc.

        For safety, the data array of the result will have a float type.
        """

        # carry out addition to a float type
        data = np.add(self.data, other, dtype=np.float)
        return Windat(self.win, data)

    def __sub__(self, other):
        """Subtracts `other` from a :class:`Windat` as `wind - other`.  Here `other`
        can be a compatible :class:`Windat` (identical window) or any object
        that can be subtracted from a :class:`numpy.ndarray`, e.g. a float,
        another matching array, etc.

        For safety, the data array of the result will have a float type.
        """
        if isinstance(other, Windat):
            # test compatibility between the windows (raises an exception)
            self.matches(other)
            num = other.data
        else:
            num = other

        # carry out addition to a float type
        data = np.subtract(self.data, num, dtype=np.float)
        return Windat(self.win, data)

    def __rsub__(self, other):
        """Subtracts a :class:`Windat` from `other` as `other - wind`.  Here `other`
        is any object that can have a :class:`numpy.ndarray` subtracted from it, e.g. a
        float, another matching array, etc.

        For safety, the data array of the result will have a float type.
        """
        # carry out subtraction to a float type
        data = np.subtract(other, self.data, dtype=np.float)
        return Windat(self.win, data)

    def __mul__(self, other):
        """Multiplies a :class:`Windat` by `other` as `wind * other`.  Here `other`
        can be a compatible :class:`Windat` (identical window) or any object
        that can multiply a :class:`numpy.ndarray`, e.g. a float, another
        matching array, etc.

        For safety, the data array of the result will have a float type.
        """
        if isinstance(other, Windat):
            # test compatibility between the windows (raises an exception)
            self.matches(other)
            num = other.data
        else:
            num = other

        # carry out multiplication to a float type
        data = np.multiply(self.data, num, dtype=np.float)
        return Windat(self.win, data)

    def __rmul__(self, other):
        """Multiplies a :class:`Windat` by `other` as `other * wind`.  Here `other` is
        any object that can multiply a :class:`numpy.ndarray`, e.g. a float,
        another matching array, etc.

        For safety, the data array of the result will have a float type.
        """
        # carry out multiplication to a float type
        data = np.multiply(self.data, other, dtype=np.float)
        return Windat(self.win, data)

    def __truediv__(self, other):
        """Divides a :class:`Windat` by `other` as `wind / other`.  Here `other`
        can be a compatible :class:`Windat` (identical window) or any object
        that can divide into a :class:`numpy.ndarray`, e.g. a float, another
        matching array, etc.

        For safety, the data array of the result will have a float type.
        """
        if isinstance(other, Windat):
            # test compatibility between the windows (raises an exception)
            self.matches(other)
            num = other.data
        else:
            num = other

        # carry out multiplication to a float type
        data = np.true_divide(self.data, num, dtype=np.float)
        return Windat(self.win, data)

    def __rtruediv__(self, other):
        """Divides `other` by a :class:`Windat` as `other / wind`.  Here `other` is
        any object that can be divided by a :class:`numpy.ndarray`, e.g. a
        float, another matching array, etc.

        For safety, the data array of the result will have a float type.
        """
        # carry out multiplication to a float type
        data = np.true_divide(other, self.data, dtype=np.float)
        return Windat(self.win, data)

# have decided that these relatively specialised routines are better as
# separate methods rather than class methods

def fitGaussian(wind, sky, height, xcen, ycen, fwhm, fwhm_min, fwhm_fix, read, gain):
    """
    Fits the profile of one target in a Windat with a symmetric 2D
    Gaussian profile given initial starting parameters. NB This will fit
    the entire Windat so normally one should window down to the object of
    interest. It returns the fitted parameters, covariances and the fit itself.
    The FWHM can be constrained to lie above a lower limit which can be useful.

    Arguments::

        wind     : (float)
            the Windat under consideration

        sky      : (float)
            value of the (assumed constant) sky background

        height   : (float)
            peak height of profile

        xcen     : (float)
            initial X value at centre of profile, unbinned absolute coordinates

        ycen     : (float)
            initial Y value at centre of profile, unbinned absolute coordinates

        fwhm     : (float)
            initial FWHM in unbinned pixels.

        fwhm_min : (float)
            minimum value to allow FWHM to go to. Useful for heavily binned data
            especially where all signal might be in a single pixel to prevent going
            to silly values.

        fwhm_fix : (bool)
            whether to hold the FWHM fixed or not.

        read     : (float)
            readout noise, RMS ADU, from which weights are generated

        gain     : (float)
            gain, e-/ADU, from which weights are generated

    Returns:: (tuple of tuples)

       (pars, sigs, extras) where::

         pars : (tuple)
            parameters. unpacks to sky, height, xcen, yce, fwhm

         sigs : (tuple)
            standard deviations. unpacks in same order, (sky, height, xcen, yce, fwhm). Can come
            back as 'None' if the fit fails so don't try to unpack on the fly.

         extras : (tuple)
            some extra stuff that might come in useful, namely (fit, X, Y, weights) where `fit`
            is a :class:Windat containing the best fit, X and Y are the X and Y positions of
            all pixels (2D numpy arrays) and weights is the final set of weights, some of which
            might = 0 indicating rejection.

    """
    global FFWHM

    # generate 2D arrays of x and y values
    x = wind.x(np.arange(wind.nx))
    y = wind.y(np.arange(wind.ny))
    X, Y = np.meshgrid(x, y)

    # generate weights
    weights = 1./np.sqrt(read**2+np.maximum(0,wind.data)/gain)

    # create symmetric 2D Gaussian model (two versions: one with
    # a fixed FWHM, the other not)
    if fwhm_fix:
        FFWHM = fwhm
        gauss = GaussianFF(sky, height, xcen, ycen)

    else:
        gauss = Gaussian(sky, height, xcen, ycen, fwhm)

        # set a minimum for fwhm
        gauss.fwhm.min = fwhm_min

    # Lev-Marq fitting
    fitter = fitting.LevMarLSQFitter()

    # run the fit
    fit = fitter(gauss, X, Y, wind.data, weights=weights)

    # extract the covariances
    cov = fitter.fit_info['param_cov']

    # compute the fit & store in a Windat
    fwind = Windat(wind, fit(X, Y))

    # extract parameters and return
    if fwhm_fix:
        pars = (fit.constant.value, fit.height.value,
                fit.xcen.value, fit.ycen.value,
                FFWHM)
    else:
        pars = (fit.constant.value, fit.height.value,
                fit.xcen.value, fit.ycen.value,
                fit.fwhm.value)

    # extract diagonal elements of covariances
    if cov is None:
        sigs = None
    else:
        if fwhm_fix:
            sigs = [np.sqrt(cov[0,0]), np.sqrt(cov[1,1]),
                    np.sqrt(cov[2,2]), np.sqrt(cov[3,3]),
                    -1]
        else:
            sigs = [np.sqrt(cov[0,0]), np.sqrt(cov[1,1]),
                    np.sqrt(cov[2,2]), np.sqrt(cov[3,3]),
                    np.sqrt(cov[4,4])]

            # set the errors if at the limit to -1
            if fit.fwhm.value == gauss.fwhm.min:
                sigs[4] = -1
        sigs = tuple(sigs)

    extras = (fwind, X, Y, weights)
    return (pars, sigs, extras)

class Gaussian(Fittable2DModel):
    """Model of a Gaussian plus a constant, with the width specified in terms
    of the FWHM rather than the standard deviation

    I did try out the compound model option of the astropy.modeling stuff but
    it did not seem to work reliably and a be-spoke model like this seems a
    fair bit easier to manage.
    """

    constant = Parameter()
    height = Parameter()
    xcen = Parameter()
    ycen = Parameter()
    fwhm = Parameter()

    EFAC = np.sqrt(8.*np.log(2.))

    @staticmethod
    def evaluate(x, y, constant, height, xcen, ycen, fwhm):
        rsq = (x-xcen)**2+(y-ycen)**2
        return constant+height*np.exp(-rsq/(2.*(fwhm/Gaussian.EFAC)**2))

    @staticmethod
    def fit_deriv(x, y, constant, height, xcen, ycen, fwhm):

        # intermediate time savers (but each the same dimension as x and y)
        xoff = x-xcen
        yoff = y-ycen
        rsq = xoff**2 + yoff**2
        alpha = 1/(2.*(fwhm/Gaussian.EFAC)**2)

        # derivatives
        d_constant = np.ones_like(x)

        d_height = np.exp(-alpha*rsq)

        d_xcen = (2*alpha*height)*d_height*xoff

        d_ycen = (2*alpha*height)*d_height*yoff

        d_fwhm = (2*alpha*height/fwhm)*d_height*rsq

        return [d_constant, d_height, d_xcen, d_ycen, d_fwhm]

class GaussianFF(Fittable2DModel):
    """Model of a Gaussian plus a constant, with the width specified in terms
    of the FWHM rather than the standard deviation. cf Gaussian this one has
    a fixed FWHM using a global value 'FFWHM'

    I did try out the compound model option of the astropy.modeling stuff but
    it did not seem to work reliably and a be-spoke model like this seems a
    fair bit easier to manage.
    """

    constant = Parameter()
    height = Parameter()
    xcen = Parameter()
    ycen = Parameter()

    EFAC = np.sqrt(8.*np.log(2.))

    @staticmethod
    def evaluate(x, y, constant, height, xcen, ycen):
        global FFWHM
        rsq = (x-xcen)**2+(y-ycen)**2
        return constant+height*np.exp(-rsq/(2.*(FFWHM/Gaussian.EFAC)**2))

    @staticmethod
    def fit_deriv(x, y, constant, height, xcen, ycen):
        global FFWHM

        # intermediate time savers (but each the same dimension as x and y)
        xoff = x-xcen
        yoff = y-ycen
        rsq = xoff**2 + yoff**2
        alpha = 1/(2.*(FFWHM/Gaussian.EFAC)**2)

        # derivatives
        d_constant = np.ones_like(x)

        d_height = np.exp(-alpha*rsq)

        d_xcen = (2*alpha*height)*d_height*xoff

        d_ycen = (2*alpha*height)*d_height*yoff

        return [d_constant, d_height, d_xcen, d_ycen]


def fitMoffat(wind, sky, height, xcen, ycen, fwhm, fwhm_min, fwhm_fix, beta, read, gain):
    """Fits the profile of one target in a Windat with a 2D Moffat profile,
    h/(1+(r/a)**2)**beta where r is the distance from the centre of the
    aperture. It returns the fitted parameters, covariances and the fit itself.
    The FWHM can be constrained to lie above a lower limit which can be useful.

    Arguments::

        sky      : (float)
            value of the (assumed constant) sky background

        height   : (float)
            peak height of profile

        xcen     : (float)
            initial X value at centre of profile, unbinned absolute coordinates

        ycen     : (float)
            initial Y value at centre of profile, unbinned absolute coordinates

        fwhm     : (float)
            initial FWHM in unbinned pixels.

        fwhm_min : (float)
            minimum value to allow FWHM to go to. Useful for heavily binned
            data especially where all signal might be in a single pixel to
            prevent going to silly values.

        fwhm_fix : (bool)
            whether or not to vary the FWHM. In some cases one may not want to
            for safety or speed.

        read     : (float)
            readout noise, RMS ADU, from which weights are generated

        gain     : (float)
            gain, e-/ADU, from which weights are generated

        sigma   : (float)
            for outlier rejection. The fit is re-run with outliers removed by
            setting their weight = 0.

    Returns:: (tuple of tuples)

        (pars, sigs, extras) where::

           pars : (tuple)
                parameters. unpacks to sky, height, xcen, yce, fwhm

           sigs : (tuple)
                standard deviations. unpacks in same order, (sky, height,
                xcen, yce, fwhm). Can come back as 'None' if the fit fails so
                don't try to unpack on the fly.

           extras : (tuple)
                some extra stuff that might come in useful, namely (fit, X, Y,
                weights) where `fit` is a :class:Windat containing the best
                fit, X and Y are the X and Y positions of all pixels (2D numpy
                arrays) and weights is the final set of weights, some of which
                might = 0 indicating rejection.

    """

    global FFWHM

    # generate 2D arrays of x and y values
    x = wind.x(np.arange(wind.nx))
    y = wind.y(np.arange(wind.ny))
    X, Y = np.meshgrid(x, y)

    # generate weights
    weights = 1./np.sqrt(read**2+np.maximum(0,wind.data)/gain)

    if fwhm_fix:
        FFWHM = fwhm

        # create a Moffat model
        moffat = MoffatFF(sky, height, xcen, ycen, beta)

    else:

        # create a Moffat model
        moffat = Moffat(sky, height, xcen, ycen, fwhm, beta)

        # set a minimum for the FWHM to prevent peaking up on single pixels
        moffat.fwhm.min = fwhm_min

    # limit the range of beta
    moffat.beta.min = 1.
    moffat.beta.max = 100.

    # Lev-Marq fitting
    fitter = fitting.LevMarLSQFitter()

    # run the fit
    fit = fitter(moffat, X, Y, wind.data, weights=weights)

    # extract the covariances
    cov = fitter.fit_info['param_cov']

    # compute the fit & store in a Windat
    fwind = Windat(wind, fit(X, Y))

    # extract parameters and return
    if fwhm_fix:
        pars = (fit.constant.value, fit.height.value,
                fit.xcen.value,fit.ycen.value,
                FFWHM,fit.beta.value)
    else:
        pars = (fit.constant.value, fit.height.value,
                fit.xcen.value,fit.ycen.value,
                fit.fwhm.value,fit.beta.value)

    # extract diagonal elements of covariances
    if cov is None:
        sigs = None
    else:
        if fwhm_fix:
            sigs = [np.sqrt(cov[0,0]), np.sqrt(cov[1,1]),
                    np.sqrt(cov[2,2]), np.sqrt(cov[3,3]),
                    -1, np.sqrt(cov[4,4])]

        else:
            sigs = [np.sqrt(cov[0,0]), np.sqrt(cov[1,1]),
                    np.sqrt(cov[2,2]), np.sqrt(cov[3,3]),
                    np.sqrt(cov[4,4]), np.sqrt(cov[5,5])]

            # set the errors on ones that are at the limit to -1
            if fit.fwhm.value == moffat.fwhm.min:
                sigs[4] = -1

        if fit.beta.value == moffat.beta.min or \
           fit.beta.value == moffat.beta.max:
            sigs[5] = -1

        sigs = tuple(sigs)

    extras = (fwind, X, Y, weights)
    return (pars, sigs, extras)

class Moffat(Fittable2DModel):

    constant = Parameter()
    height = Parameter()
    xcen = Parameter()
    ycen = Parameter()
    fwhm = Parameter()
    beta = Parameter()

    # In code below, Moffat written as c + h/(1+alpha*r**2)**beta where alpha
    # = 4*(2**(1/beta)-1)/fwhm**2 and r**2 = (x-x0)**2+(y-y0)**2

    @staticmethod
    def evaluate(x, y, constant, height, xcen, ycen, fwhm, beta):
        rsq = (x-xcen)**2+(y-ycen)**2
        alpha = 4*(2**(1/beta)-1)/fwhm**2
        return constant+height/(1+alpha*rsq)**beta

    @staticmethod
    def fit_deriv(x, y, constant, height, xcen, ycen, fwhm, beta):

        # intermediate time savers (but each the same dimension as x and y)
        xoff = x-xcen
        yoff = y-ycen
        rsq = xoff**2 + yoff**2
        alpha = 4*(2**(1/beta)-1)/fwhm**2
        denom = 1+alpha*rsq
        save1 = height/denom**(beta+1)
        save2 = save1*rsq

        # derivatives. beta is a bit complicated because it appears directly
        # through the exponent but also indirectly through alpha
        d_constant = np.ones_like(x)

        d_height = 1/denom**beta

        d_xcen = (2*alpha*beta)*xoff*save1

        d_ycen = (2*alpha*beta)*yoff*save1

        d_fwhm = (2*alpha*beta/fwhm)*save2

        d_beta = -np.log(denom)*height*d_height + (4.*np.log(2)*2**(1/beta)/beta/fwhm**2)*save2

        return [d_constant, d_height, d_xcen, d_ycen, d_fwhm, d_beta]

class MoffatFF(Fittable2DModel):

    constant = Parameter()
    height = Parameter()
    xcen = Parameter()
    ycen = Parameter()
    beta = Parameter()

    # In code below, Moffat written as c + h/(1+alpha*r**2)**beta where alpha
    # = 4*(2**(1/beta)-1)/fwhm**2 and r**2 = (x-x0)**2+(y-y0)**2

    @staticmethod
    def evaluate(x, y, constant, height, xcen, ycen, beta):
        global FFWHM
        rsq = (x-xcen)**2+(y-ycen)**2
        alpha = 4*(2**(1/beta)-1)/FFWHM**2
        return constant+height/(1+alpha*rsq)**beta

    @staticmethod
    def fit_deriv(x, y, constant, height, xcen, ycen, beta):
        global FFWHM

        # intermediate time savers (but each the same dimension as x and y)
        xoff = x-xcen
        yoff = y-ycen
        rsq = xoff**2 + yoff**2
        alpha = 4*(2**(1/beta)-1)/FFWHM**2
        denom = 1+alpha*rsq
        save1 = height/denom**(beta+1)
        save2 = save1*rsq

        # derivatives. beta is a bit complicated because it appears directly
        # through the exponent but also indirectly through alpha
        d_constant = np.ones_like(x)

        d_height = 1/denom**beta

        d_xcen = (2*alpha*beta)*xoff*save1

        d_ycen = (2*alpha*beta)*yoff*save1

        d_beta = -np.log(denom)*height*d_height + (4.*np.log(2)*2**(1/beta)/beta/FFWHM**2)*save2

        return [d_constant, d_height, d_xcen, d_ycen, d_beta]

def combFit(wind, method, sky, height, x, y,
            fwhm, fwhm_min, fwhm_fix, beta, read, gain):
    """
    Fits a stellar profile in a :class:Windat using either a 2D Gaussian
    or Moffat profile. This is a convenience routine because one practically
    always wants both options.

    Arguments::

        wind      : (:class:Windat)
            the Windat containing the stellar profile to fit

        method    : (string)
            fitting method 'g' for Gaussian, 'm' for Moffat

        sky       : (float)
            initial sky level

        height    : (float)
            initial peak height

        x         : (float)
            initial central X value

        y         : (float)
            initial central Y value

        fwhm      : (float)
            initial FWHM, unbinned pixels.

        fwhm_min  : (float)
            minimum FWHM, unbinned pixels.

        fwhm_fix  : (float)
            fix the FWHM (i.e. don't fit it)

        beta      : (float) [if method == 'm']
            exponent of Moffat function

        read      : (float)
            readout noise, RMS ADU

        gain      : (float)
            gain, electrons per ADU

    Returns:: (pars, epars, extras)

    where::

       pars    : (tuple)
          Fitted parameters: (sky, height, x, y, fwhm, beta)
          beta is None if method=='g'

       epars   : (tuple)
          Fitted uncertainties: (esky, eheight, ex, ey, efwhm, ebeta)
          ebeta is None if method=='g'

       extras  : (tuple)
          (X,Y,message) -- X and Y are the X and Y coordinates of
          the pixels. message summarises the fit values.

    Raises a HipercamError if the fit fails.
    """

    if method == 'g':
        # gaussian fit
        (sky, height, x, y, fwhm), sigs, \
            (fit, X, Y, weights) = fitGaussian(
                wind, sky, height, x, y, fwhm, fwhm_min, fwhm_fix, read, gain
            )

    elif method == 'm':
        # moffat fit
        (sky, height, x, y, fwhm, beta), sigs, \
            (fit, X, Y, weights) = fitMoffat(
                wind, sky, height, x, y, fwhm, fwhm_min, fwhm_fix, beta, read, gain
            )

    else:
        raise NotImplementedError(
            '{:s} fitting method not implemented'.format(method)
        )

    if sigs is None:
        raise HipercamError('fit failed')

    if method == 'g':
        esky, eheight, ex, ey, efwhm = sigs
        message = 'x,y = {:.1f}({:.1f}),{:.1f}({:.1f}), FWHM = {:.2f}({:.2f}), peak = {:.1f}({:.1f}), sky = {:.1f}({:.1f})'.format(
            x,ex,y,ey,fwhm,efwhm,height,eheight,sky,esky)
        beta, ebeta = None, None
    elif method == 'm':
        esky, eheight, ex, ey, efwhm, ebeta = sigs
        message = 'x,y = {:.1f}({:.1f}),{:.1f}({:.1f}), FWHM = {:.2f}({:.2f}), peak = {:.1f}({:.1f}), sky = {:.1f}({:.1f}), beta = {:.2f}({:.2f})'.format(
            x,ex,y,ey,fwhm,efwhm,height,eheight,sky,esky,beta,ebeta)

    return (
        (sky, height, x, y, fwhm, beta),
        (esky, eheight, ex, ey, efwhm, ebeta),
        (X, Y, message)
    )
