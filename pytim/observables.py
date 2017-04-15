# -*- Mode: python; tab-width: 4; indent-tabs-mode:nil; coding: utf-8 -*-
# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
""" Module: observables
    ===================
"""
from abc import ABCMeta, abstractmethod
import numpy as np
from scipy import stats
try: # MDA >=0.16
    from  MDAnalysis.core.groups import *
except: # MDA 0.15
    from MDAnalysis.core.AtomGroup import *

from MDAnalysis.analysis import rdf
from MDAnalysis.lib import distances
from itertools import chain
import pytim
import utilities
# we try here to have no options passed
# to the observables, so that classes are
# not becoming behemoths that do everything.
# Simple preprocessing (filtering,...)
# should be done by the user beforehand,
# or implemented in specific classes.

class Observable(object):
    """ Instantiate an observable.

    """
    __metaclass__ = ABCMeta

    def __init__(self,universe,options='',):
        self.u=universe
        self.options=options

    #TODO: add proper whole-molecule reconstruction
    def fold_atom_around_first_atom_in_residue(self,atom):
        # let's thake the first atom in the residue as the origin
        box=self.u.trajectory.ts.dimensions[0:3]
        pos = atom.position - atom.residue.atoms[0].position
        pos[pos>= box/2.]-=box[pos>= box/2.]
        pos[pos<-box/2.]+=box[pos<-box/2.]
        return pos

    def fold_around_first_atom_in_residue(self,inp):
        pos=[]

        if isinstance(inp,Atom):
                pos.append(self.fold_atom_around_first_atom_in_residue(inp))
        elif isinstance(inp, AtomGroup) or \
                isinstance(inp, Residue) or \
                isinstance(inp, ResidueGroup):
                for atom in inp.atoms:
                    pos.append(self.fold_atom_around_first_atom_in_residue(atom))
        else:
            raise Exception("input not valid for fold_around_first_atom_in_residue()")
        return np.array(pos)

    @abstractmethod
    def compute(self,input):
        pass


class RDF(object):
    """ Calculates a radial distribution function of some observable from two groups.

        The two functions must return an array (of scalars or of vectors)
        having the same size of the group. The scalar product between the
        two functions is used to weight the distriution function.

        .. math::

              g(r) = \\frac{1}{N}\left\langle \sum_{i\\neq j} \delta(r-|r_i-r_j|) f_1(r_i,v_i)\cdot f_2(r_j,v_j) \\right\\rangle


        :param AtomGroup g1:            1st group
        :param AtomGroup g2:            2nd group
        :param double range:            compute the rdf up to this distance. If 'full' is supplied (default) computes it up to half of the smallest box side.
        :param int nbins:               number of bins
        :param char excluded_dir:       project position vectors onto the plane orthogonal to 'z','y' or 'z' (TODO not used here, check & remove)
        :param Observable observable:   observable calculated on the atoms in g1
        :param Observable observable2:  observable calculated on the atoms in g2
        :param array weights:           weights to be applied to the distribution function (mutually exclusive with observable/observable2)

    """

    def __init__(self, universe,
                 nbins=75, range='full', exclusion_block=None,
                 start=None, stop=None, step=None,excluded_dir='z',
                 observable=None,observable2=None,weights=None):
        if range is 'full':
            self.range=np.min(universe.dimensions[:3])
        else:
            self.range=range
        self.rdf_settings = {'bins': nbins,
                             'range': (0.,self.range)}
        self.universe = universe
        self.nsamples=0
        self.observable=observable
        self.observable2=observable2
        self.weights=weights
        self.n_frames = 0
        self.volume=0.0
        count, edges = np.histogram([-1,-1], **self.rdf_settings)
        self.count = count * 0.0
        self.edges = edges
        self.bins = 0.5 * (edges[:-1] + edges[1:])

    def sample(self,g1,g2):
        self.n_frames +=1
        self.g1 = g1
        self.g2 = g2
        self._ts=self.universe.trajectory.ts
        if (self.observable is not None or
            self.observable2 is not None) and \
            self.weights is not None:
            raise Exception("Error, cannot specify both a function and weights in RDF()" )
        if self.observable is not None or self.observable2 is not None:
            if self.observable2 is None:
                self.observable2 = self.observable

                fg1 = self.observable.compute(self.g1)
                fg2 = self.observable2.compute(self.g2)
                if len(fg1)!=len(self.g1) or len(fg2)!=len(self.g2):
                    raise Exception ("Error, the observable passed to RDF should output an array (of scalar or vectors) the same size of the group")
                # both are (arrays of) scalars
                if len(fg1.shape)==1 and len(fg2.shape)==1:
                    _weights = np.outer(fg1,fg2)
                # both are (arrays of) vectors
                elif len(fg1.shape)==2 and len(fg2.shape)==2:
                # TODO: tests on the second dimension...
                    _weights = np.dot(fg1,fg2.T)
                else :
                    raise Exception("Error, shape of the observable output not handled in RDF")
                # numpy.histogram accepts negative weights
                self.rdf_settings['weights']=_weights
        if self.weights is not None:
            raise Exception("Weights not implemented yet in RDF")

        # This still uses MDA's distance_array. Pro: works also in triclinic boxes. Con: could be faster (?)
        _distances = np.zeros((len(self.g1), len(self.g2)), dtype=np.float64)
        distances.distance_array(self.g1.positions, self.g2.positions,
                                 box=self.universe.dimensions, result=_distances)

        count = np.histogram(_distances, **self.rdf_settings)[0]
        self.count += count

        try:
            self.volume += self._ts.volume
        except:
            self.volume += self.universe.dimensions[0]*self.universe.dimensions[1]*self.universe.dimensions[2]
        self.nsamples+=1

    @property
    def rdf(self):
        nA = len(self.g1)
        nB = len(self.g2)
        N = nA * nB
        # Volume in each radial shell
        vol = 4./3. * np.pi * np.power(self.edges[1:], 3) - np.power(self.edges[:-1], 3)

        # Average number density
        box_vol = self.volume / self.n_frames
        density = N / box_vol

        self._rdf = self.count / (density * vol * self.n_frames)

        return self._rdf


class RDF2D(RDF):
    """ Calculates a radial distribution function of some observable from two groups, projected on a plane.

        The two functions must return an array (of scalars or of vectors)
        having the same size of the group. The scalar product between the
        two functions is used to weight the distriution function.

        :param AtomGroup g1:            1st group
        :param AtomGroup g2:            2nd group
        :param int nbins:               number of bins
        :param ??? exclusion_block:
        :param int start:               first frame
        :param int stop:                last frame
        :param int step:                frame stride
        :param char excluded_dir:       project position vectors onto the plane orthogonal to 'z','y' or 'z' (TODO not used here, check & remove)
        :param Observable observable:        observable calculated on the atoms in g1
        :param Observable observable2:       observable calculated on the atoms in g2
        :param array weights:           weights to be applied to the distribution function (mutually exclusive with observable/observable2)

        Example:

        >>> import MDAnalysis as mda
        >>> import numpy as np
        >>> import pytim
        >>> from pytim import *
        >>> from pytim.datafiles import *
        >>>
        >>> u = mda.Universe(WATER_GRO,WATER_XTC)
        >>> oxygens = u.select_atoms("name OW")
        >>> radii=pytim_data.vdwradii(G43A1_TOP)
        >>> rdf = observables.RDF2D(u,nbins=120)
        >>> interface = pytim.ITIM(u,alpha=2.,itim_group=oxygens,max_layers=4,radii_dict=radii,cluster_cut=3.5)
        >>>
        >>> for ts in u.trajectory[::50] :
        ...     layer=interface.layers[0,1]
        ...     rdf.sample(layer,layer)
        >>> rdf.rdf[0]=0.0
        >>> np.savetxt('RDF.dat', np.column_stack((rdf.bins,rdf.rdf)))  #doctest:+SKIP


        This results in the following RDF:

        .. plot::

            import MDAnalysis as mda
            import numpy as np
            import pytim
            import matplotlib.pyplot as plt
            from   pytim.datafiles import *
            u = mda.Universe(WATER_GRO,WATER_XTC)
            L = np.min(u.dimensions[:3])
            oxygens = u.select_atoms("name OW")
            radii=pytim_data.vdwradii(G43A1_TOP)
            interface = pytim.ITIM(u,alpha=2.,itim_group=oxygens,max_layers=4,multiproc=True,radii_dict=radii,cluster_cut=3.5)
            rdf=pytim.observables.RDF2D(u,nbins=120)
            for ts in u.trajectory[::50] :
                layer=interface.layers[0,1]
                rdf.sample(layer,layer)
            rdf.rdf[0]=0.0
            plt.plot(rdf.bins, rdf.rdf)
            plt.show()


    """

    def __init__(self, universe,
                 nbins=75, range='full', exclusion_block=None,
                 start=None, stop=None, step=None,excluded_dir='z',
                 true2D=False, observable=None):
        RDF.__init__(self, universe,nbins=nbins, range=range,
                          exclusion_block=exclusion_block,
                          start=start, stop=stop, step=step,
                          observable=observable)
        self.true2D=true2D
        if excluded_dir is 'z':
                self.excluded_dir=2
        if excluded_dir is 'y':
                self.excluded_dir=1
        if excluded_dir is 'x':
                self.excluded_dir=0

    def sample(self,g1,g2):
        self.n_frames+=1
        excl=self.excluded_dir
        if self.true2D:
            p1=g1.positions
            p2=g2.positions
            self._p1=np.copy(p1)
            self._p2=np.copy(p2)
            p1[:,excl]=0
            p2[:,excl]=0
        RDF.sample(self,g1,g2)
        if self.true2D:
            self.g1.positions = np.copy(self._p1)
            self.g1.positions = np.copy(self._p2)
        # TODO: works only for rectangular boxes
        # we subtract the volume added for the 3d case,
        # and we add the surface
        self.volume += self._ts.volume*(1./self._ts.dimensions[excl]-1.)

    @property
    def rdf(self):
        nA = len(self.g1)
        nB = len(self.g2)
        N = nA * nB
        # Volume in each radial shell
        vol = 4.0 * np.pi * np.power(self.edges[1:], 2) - np.power(self.edges[:-1], 2)

        # Average number density
        box_vol = self.volume / self.n_frames
        density = N / box_vol

        self._rdf = self.count / (density * vol * self.n_frames)

        return self._rdf


class LayerTriangulation(Observable):
    """ Computes the triangulation of the surface and some associated quantities
            :param Universe universe: the MDAnalysis universe
            :param ITIM    interface: compute the triangulation with respect to this interface
            :param int     layer: (default: 1) compute the triangulation with respect to this layer of the interface
            :param bool    return_triangulation: (default: True) return the Delaunay triangulation used for the interpolation
            :param bool    return_statistics: (default: True) return the Delaunay triangulation used for the interpolation

            :returns Observable LayerTriangulation:
    """

    def __init__(self,interface,layer=1,return_triangulation=True,return_statistics=True):
        self.interface=interface
        self.layer=layer
        self.return_triangulation=return_triangulation
        self.return_statistics=return_statistics

    def compute(self,input=None):
        """ Compute the triangulation of a layer on both sides of the interface

            Example:

            >>> interface = pytim.ITIM(mda.Universe(WATER_GRO))
            >>> surface   = observables.LayerTriangulation(interface,return_triangulation=False)
            >>> stats     = surface.compute()
            >>> print ("Surface= {:04.1f} A^2".format(stats[0]))
            Surface= 7317.1 A^2

        """
        stats = []
        self.interface.triangulate_layer(self.layer)
        box = self.interface.universe.dimensions[:3]
        if self.return_triangulation is True and self.return_statistics is False:
            return self.interface.surface_triangulation
        if self.return_statistics is True:
            stats_up  = utilities.triangulated_surface_stats(self.interface.trimmed_surface_triangles[0],
                                                             self.interface.triangulation_points[0])
            stats_low = utilities.triangulated_surface_stats(self.interface.trimmed_surface_triangles[1],
                                                             self.interface.triangulation_points[1])
            # this average depends on what is in the stats, it can't be done automatically
            stats.append(stats_up[0]+stats_low[0])
            # add here new stats other than total area
            if self.return_triangulation is False :
                return stats
            else:
                return [stats, self.interface.surface_triangulation, self.interface.triangulation_points, self.interface.trimmed_surface_triangles ]


class IntrinsicDistance(Observable):
    """ Initialize the intrinsic distance calculation
            :param Universe universe: the MDAnalysis universe
            :param ITIM    interface: compute the intrinsic distance with respect to this interface
            :param int     layer: (default: 1) compute the intrinsic distance with respect to this layer of the interface
            :param bool    return_triangulation: (default: False) return the Delaunay triangulation used for the interpolation

        Example: TODO
    """

    def __init__(self,interface,layer=1,return_triangulation=False):
        self.interface=interface
        self.return_triangulation=return_triangulation
        self.layer=layer

    def compute(self,input):
        """ Compute the intrinsic distance of a set of points from the first layers
            :param ndarray positions: compute the intrinsic distance for this set of points

            Example: TODO

        """
        t = type(input)
        if t is np.ndarray:
            positions = input
        if t is Atom:
            positions=input.position
        if t is AtomGroup:
            positions=input.positions
        elevation = self.interface.interpolate_surface(positions,self.layer)
        assert np.sum(np.isnan(elevation))==0 , "Internal error: a point has fallen outside the convex hull"
        # positive values are outside the surface, negative inside
        distance  = (positions[:,2]-elevation) * np.sign(positions[:,2])
        if self.return_triangulation == False:
            return distance
        else:
            return [distance, interface.surface_triangulation[0], interface.surface_triangulation[1]]


class Number(Observable):
    """ The number of atoms

    """
    def __init__(self):
        pass

    def compute(self,inp):
        """ Compute the observable

        :param AtomGroup inp:  the input atom group
        :returns: one, for each atom in the group

        """
        return np.ones(len(inp))

class NumberOfResidues(Observable):
    """ The number of residues. Instead of associating 1 to the center of mass
        of the residue, we associate 1/(number of atoms in residue) to each atom.
        In an homogeneous system, these two definitions are (on average) equivalent.
        If the system is not homogeneous, this is not true anymore.

    """
    def __init__(self):
        pass

    def compute(self,inp):
        """ Compute the observable

        :param AtomGroup inp:  the input atom group
        :returns: one, for each residue in the group

        """
        residueNames = np.unique(inp.atoms.resnames)
        tmp = np.zeros(len(inp))

        for name in residueNames:
             atomId = np.where(inp.resnames==name)[0][0]
             tmp[ inp.resnames==name ] = 1./len(inp[atomId].residue.atoms)

        return tmp




class Orientation(Observable):
    """ Orientation of a group of points

        :param str options: optional string. If `normal` is passed, the orientation of the normal vectors is computed

        This observable does not take into account boudary conditions. See :class:`~pytim.observables.MolecularOrientation`

    """

    def __init__(self,options=''):
        self.options = options

    def compute(self,pos):
        """ Compute the observable

        :param ndarray inp:  the input atom group. The length be a multiple of three
        :returns: the orientation vectors

        For each triplet of positions A1,A2,A3, computes the unit vector beteeen A2-A1 and  A3-A1 or,
        if the option 'normal' is passed at initialization, the unit vector normal to the plane
        spanned by the three vectors


        """

        flat = pos.flatten()
        pos  = flat.reshape(len(flat)/3,3)
        a = pos[1::3] - pos[0::3]
        b = pos[2::3] - pos[0::3]
        # TODO: can this be vectorized?
        if 'normal' in self.options:
            v = np.cross(a,b)
        else:
            v = np.array(a+b)
        v =  np.array([el/np.sqrt(np.dot(el,el)) for el in v])
        return v

class MolecularOrientation(Observable):
    """ Molecular orientation vector of a set of molecules

    """
    def compute(self,inp):
        """ Compute the observable

            :param variable inp: an AtomGroup or a ResidueGroup

            TODO: document and check if the function needs to be modified

        """
        t = type(inp)
        # TODO: checks for other types?
        if t is AtomGroup and len(inp) != 3*len(inp.residues):
            #TODO: we take automatically the first three if more than three are supplied?
            inp=inp.residues
        pos = self.fold_around_first_atom_in_residue(inp)
        return Orientation().compute(pos)



class Profile(object):
    """ Calculates the profile (normal, or intrinsic) of a given observable across the simulation box

        :param AtomGroup    group:          calculate the profile based on this group
        :param str          direction:      'x','y', or 'z' : calculate the profile along this direction
        :param Observable   observable:     calculate the profile of this quantity. If None is supplied, it defaults to the number density
        :param ITIM         interface:      if provided, calculate the intrinsic profile with respect to the first layers
        :param AtomGroup    center_group:   if `interface` is not provided, this optional group can be supplied to center the system

        Example:

        >>> u       = mda.Universe(WATER_GRO,WATER_XTC)
        >>> oxygens = u.select_atoms("name OW")
        >>> radii=pytim_data.vdwradii(G43A1_TOP)
        >>>
        >>> obs     = observables.Number()
        >>> profile = observables.Profile(group=oxygens,observable=obs)
        >>>
        >>> interface = pytim.ITIM(u, alpha=2.0, max_layers=1,cluster_cut=3.5)
        >>>
        >>> for ts in u.trajectory[::50]:
        ...     interface.center(oxygens)
        ...     profile.sample()
        >>>
        >>> low, up, avg = profile.get_values(binwidth=1.0)
        >>> np.savetxt('profile.dat',list(zip(low,up,avg)))


        This results in the following profile:

        .. plot::

            import MDAnalysis as mda
            import numpy as np
            import pytim
            import matplotlib.pyplot as plt
            from   pytim.datafiles   import *

            u       = mda.Universe(WATER_GRO,WATER_XTC)
            oxygens = u.select_atoms("name OW")
            radii=pytim_data.vdwradii(G43A1_TOP)

            obs     = pytim.observables.Number()
            profile = pytim.observables.Profile(group=oxygens,observable=obs)

            interface = pytim.ITIM(u, alpha=2.0, max_layers=1,cluster_cut=3.5)

            for ts in u.trajectory[::50]:
                interface.center(oxygens)
                profile.sample()

            low, up, avg = profile.get_values(binwidth=1.0)
            plt.plot((low+up)/2., avg)
            plt.show()

    """

    def __init__(self,group,direction='z',observable=None,interface=None,center_group=None):
        #TODO: the directions are handled differently, fix it in the code
        _dir = {'x':0,'y':1,'z':2}
        self.halfbox_shift = False
        self.group         = group

        assert isinstance(group,AtomGroup) , "The first argument passed to Profile() must be an AtomGroup."
        self.universe      = group.universe
        self.center_group  = center_group
        if observable is None:
            self.observable = Number()
        self.observable    = observable
        self._dir          =_dir[direction]
        self.binsize       = 0.1 # this is used for internal calculations, the output binsize can be specified in self.get_values()

        self.interface     = interface
        if self.interface is not None:
            self.halfbox_shift=True
        self.sampled_values=[]
        self.sampled_bins  =[]
        self.pos=[utilities.get_x,utilities.get_y,utilities.get_z]

    def sample(self):
        # TODO: implement progressive averaging to handle very long trajs
        # TODO: implement memory cleanup
        self._box=self.universe.dimensions[:3]
        self.do_rebox=False

        # We need to decide if we have to rebox particles or not. If the trajectory is patched, it means
        # that the coordinates have been centered using the default scheme of each method, and we can handle this.
        # Otherwise, we rebox.

        try:
            id(self.universe.trajectory.interface)>0
            _range =[-self._box[self._dir]/2.,self._box[self._dir]/2.]
            self.do_rebox=False
        except:
            _range =[0.,self._box[self._dir]]
            self.do_rebox=True

        if self.interface is None:
            # non-intrinsic quantities are sampled
            _pos    = np.copy(self.pos[self._dir](self.group)) # TODO: check if this was returning already a new object
            try:
                id(self.universe.trajectory)>0
                self.universe.atoms.positions=np.copy(self.oldpos)
            except:
                pass

            if self.do_rebox==True:
                condition = _pos>=self._box[self._dir]
                _pos[condition]-=self._box[self._dir]
                condition = _pos<0
                _pos[condition]+=self._box[self._dir]
        else:
            _pos    = IntrinsicDistance(self.interface).compute(self.group)

        _values = self.observable.compute(self.group)
        _nbins  = int(self.universe.trajectory.ts.dimensions[self._dir]/self.binsize)
        # we need to make sure that the number of bins is odd, so that the central one encompasses
        # zero (to make the delta-function contribution appear always in this bin)
        if(_nbins % 2 > 0 ):
            _nbins-=1
        _avg, _bins, _binnumber = stats.binned_statistic(_pos, _values,
                                                         range=_range,
                                                         statistic='sum',bins=_nbins)
        _avg[np.isnan(_avg)]=0.0
        self.sampled_values.append(_avg)
        self.sampled_bins.append(_bins[1:]-self.binsize/2.) # these are the bins midpoints

    def get_values(self,binwidth=None,nbins=None):
        assert self.sampled_values ,  "No profile sampled so far."
        # we use the largest box (largest number of bins) as reference.
        # Statistics will be poor at the boundaries, but like that we don't loose information
        _max_bins  = np.max(map(lambda x: len(x),self.sampled_bins))
        _max_size  = _max_bins * self.binsize
        if(binwidth==None and nbins==None):
            _nbins = _max_bins
        else:
            if binwidth==None:
                _nbins = nbins
            else:
                _nbins = _max_size/binwidth
        if(_nbins % 2 > 0 ):
            _nbins+=1

        # TODO sanity check on binwidth and nbins missing
        if self.halfbox_shift is True:
            _range = [-self._box[self._dir]/2.,self._box[self._dir]/2.]
        else:
            _range = [0,self._box[self._dir]]
        _avg,_bins,_binnumber = stats.binned_statistic(list(chain.from_iterable(self.sampled_bins  )),
                                            list(chain.from_iterable(self.sampled_values)),
                                            range=_range,
                                            statistic='sum',bins=_nbins )
        _avg[np.isnan(_avg)]=0.0
        _binsize = _bins[1]-_bins[0]
        _vol = np.prod(self._box)/_nbins
        return [  _bins[0:-1], _bins[1:] , _avg/len(self.sampled_values)/_vol ]





#
