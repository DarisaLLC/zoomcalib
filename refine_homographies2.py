#! /usr/bin/python

import os.path
import numpy as np
import cPickle as pickle
from collections import OrderedDict
from scipy.optimize import root

from projective_math import SqExpWeightingFunction
from camera_math import estimate_intrinsics_noskew_assume_cxy
from camera_math import estimate_intrinsics_noskew
from camera_math import get_extrinsics_from_homography
from camera_math import matrix_to_xyzrph, matrix_to_intrinsics
from camera_math import xyzrph_to_matrix, intrinsics_to_matrix
from tupletypes import WorldImageHomographyInfo



#--------------------------------------
class HomographyModel(object):
#--------------------------------------
    """
    Encapsulation of the data in `WorldImageHomographyInfo`
    and methods that act on that data

    Members:
    --------
        `hinfo`: `WorldImageHomographyInfo` object
         `itag`: intrinsics tag
         `etag`: extrinsics tag
    """
    def __init__(self, hinfo):
        self.hinfo = hinfo
        self.etag = None
        self.itag = None
        self.H0 = None


    @classmethod
    def load_from_file(class_, filename):
        # parse the filename to get intrinsic/extrinsic tags
        itag, etag = filename.split('/')[-2:]
        etag = etag.split('.')[0]

        with open(filename) as f:
            hinfo = pickle.load(f)

        # create and populate instance
        instance = class_(hinfo)
        instance.etag = etag
        instance.itag = itag
        return instance


    def homography_at_center(self):
        if self.H0 is None:
            H_wi, c_w, _ = self.hinfo
            self.H0 = H_wi.get_homography_at(c_w)

        return self.H0


#-------------------------------------
class IntrinsicsNode(object):
#-------------------------------------
    def __init__(self, fx, fy, cx, cy, tag):
        self.fx = fx
        self.fy = fy
        self.cx = cx
        self.cy = cy
        self.tag = tag # convenient identification

    def to_tuple(self):
        return self.fx, self.fy, self.cx, self.cy

    def set_value(self, *tupl):
        self.fx = tupl[0]
        self.fy = tupl[1]
        self.cx = tupl[2]
        self.cy = tupl[3]

    def __repr__(self):
        return repr(self.to_tuple())

    def to_matrix(self):
        return intrinsics_to_matrix(*self.to_tuple())


#-------------------------------------
class ExtrinsicsNode(object):
#-------------------------------------
    def __init__(self, x, y, z, r, p, h, tag):
        self.x = x
        self.y = y
        self.z = z
        self.r = r
        self.p = p
        self.h = h
        self.tag = tag # convenient identification

    def to_tuple(self):
        return self.x, self.y, self.z, self.r, self.p, self.h

    def set_value(self, *tupl):
        self.x = tupl[0]
        self.y = tupl[1]
        self.z = tupl[2]
        self.r = tupl[3]
        self.p = tupl[4]
        self.h = tupl[5]

    def __repr__(self):
        return repr(self.to_tuple())

    def to_matrix(self):
        return xyzrph_to_matrix(*self.to_tuple())


#--------------------------------------
class HomographyConstraint(object):
#--------------------------------------
    def __init__(self, hmodel, inode, enode):
        H_wi, c_w, _ = hmodel.hinfo
        weights = H_wi.get_correspondence_weights(c_w)

        # 3-D homogeneous form with z=0
        p_src = [ c.source for c in H_wi._corrs ]
        N = len(p_src)
        p_src = np.hstack([ p_src, np.zeros((N,1)), np.ones((N,1)) ])

        self.p_src = p_src.T
        self.p_tgt = np.array([ c.target for c in H_wi._corrs ]).T
        self.W = np.diag(weights)
        self.inode = inode
        self.enode = enode

    def sq_unweighted_reprojection_errors(self):
        """
        compute the geometric reprojection error of the world
        points `self.p_w` through the homography described
        the composition of intrinsics and extrinsics.
        """
        K = self.inode.to_matrix()
        E = self.enode.to_matrix()
        H = K.dot(E)

        p_mapped = H.dot(self.p_src)[:3,:]

        # normalize homogeneous coordinates
        M = np.diag(1./p_mapped[2,:])
        p_mapped = p_mapped[:2,:].dot(M)

        return ((p_mapped - self.p_tgt)**2)


    def sq_errors(self):
        sqerr = self.sq_unweighted_reprojection_errors().ravel()
        abserr = np.sqrt(sqerr)

        def pseudo_huber_loss(delta):
            return np.where(abserr <= delta, sqerr/2., delta*(abserr - delta/2.))

        return pseudo_huber_loss(1.)


#--------------------------------------
class ConstraintGraph(object):
#--------------------------------------
    def __init__(self):
        self.inodes = OrderedDict()
        self.enodes = OrderedDict()
        self.constraints = list()


    def constraint_errors(self):
        return np.hstack([ c.sq_errors() for c in self.constraints ])


    def sq_pixel_errors(self):
        homography_constraints = ( c for c in self.constraints if isinstance(c, HomographyConstraint) )
        return np.hstack([ c.sq_unweighted_reprojection_errors() for c in homography_constraints ])


    def _pack_into_vector(self):
        """ pack node states into a vector """
        istate = np.hstack([ i.to_tuple() for i in self.inodes.values() ])
        estate = np.hstack([ e.to_tuple() for e in self.enodes.values() ])
        return np.hstack(( istate, estate ))


    def _unpack_from_vector(self, v):
        """ Set node values from the vector `v` """
        N = len(self.inodes)
        istate = np.reshape(v[:4*N], (-1, 4))
        estate = np.reshape(v[4*N:], (-1, 6))

        for inode, ival in zip(self.inodes.values(), istate):
            inode.set_value(*ival)

        for enode, eval_ in zip(self.enodes.values(), estate):
            enode.set_value(*eval_)


    state = property(_pack_into_vector, _unpack_from_vector)


    def _pack_intrinsics_into_vector(self):
        """ pack intrinsic node states into a vector """
        return np.hstack([ i.to_tuple() for i in self.inodes.values() ])


    def _unpack_intrinsics_from_vector(self, v):
        """ Set intrinsic node values from the vector `v` """
        istate = np.reshape(v, (-1, 4))

        for inode, ival in zip(self.inodes.values(), istate):
            inode.set_value(*ival)


    istate = property(_pack_intrinsics_into_vector, _unpack_intrinsics_from_vector)



def main():
    import sys
    from glob import iglob
    from itertools import groupby

    np.set_printoptions(precision=4, suppress=True)

    folder = sys.argv[1]
    saved_files = iglob(folder + '/*/*.lh0')
    hmodels = [ HomographyModel.load_from_file(f) for f in saved_files ]
    print '%d hmodels\n' % len(hmodels)

    #
    # Deconstruct information in the HomographyModels into
    # a graph of nodes and constraints
    #
    graph = ConstraintGraph()

    itag_getter = lambda e: e.itag
    hmodels.sort(key=itag_getter)

    for itag, group in groupby(hmodels, key=itag_getter):
        group = list(group)
        homographies = [ hm.homography_at_center() for hm in group ]

        K = estimate_intrinsics_noskew(homographies)
        inode = IntrinsicsNode(*matrix_to_intrinsics(K), tag=itag)

        # For each HomographyModel in `group` construct an ExtrinsicsNode
        enode_tags = [ '%s/%s' % (itag, hm.etag) for hm in group ]
        E_matrices = ( get_extrinsics_from_homography(H, K) for H in homographies )
        enodes = [ ExtrinsicsNode(*matrix_to_xyzrph(E), tag=tag) for E, tag in zip(E_matrices, enode_tags) ]

        # Instantiate constraints between each ExtrinsicsNode and the single IntrinsicsNode
        constraints = [ HomographyConstraint(hm, inode, enode) for hm, enode in zip(group, enodes) ]

        # Leave out the worst two constraints
        if False:
            rmse = [ c.sq_unweighted_reprojection_errors().mean() for c in constraints ]
            inliner_idx = np.argsort(rmse)[:-2]

            enodes = [ enodes[i] for i in inliner_idx ]
            constraints = [ constraints[i] for i in inliner_idx ]

        # Add nodes and constraints to graph
        graph.inodes[itag] = inode
        graph.enodes.update( (e.tag, e) for e in enodes )
        graph.constraints.extend( constraints )

    print 'Graph'
    print '-----'
    print '  %d intrinsic nodes' % len(graph.inodes)
    print '  %d extrinsic nodes' % len(graph.enodes)
    print '  %d constraints' % len(graph.constraints)
    print ''
    for constraint in (c for c in graph.constraints if isinstance(c, HomographyConstraint)):
        rmse = np.sqrt(constraint.sq_unweighted_reprojection_errors().mean())
        print '  %s rmse: %.2f' % (constraint.enode.tag, rmse)

    #
    # Optimize graph to reduce error in constraints
    #
    def print_graph_summary(title):
        print '\n' + title
        print '-'*len(title)
        print '    rmse: %.4f' % np.sqrt(graph.sq_pixel_errors().mean())
        print '  rmaxse: %.4f' % np.sqrt(graph.sq_pixel_errors().max())
        print ''
        for itag, inode in graph.inodes.iteritems():
            print '  intrinsics@ ' + itag + " =", np.array(inode.to_tuple())

    def objective(x):
        graph.state = x
        return graph.constraint_errors()

    def optimize_graph():
        x0 = graph.state
        print_graph_summary('Initial:')

        print '\nOptimizing graph ...'
        result = root(objective, x0, method='lm', options={'factor': 0.1, 'col_deriv': 1})
        print '  Success: ' + str(result.success)
        print '  %s' % result.message

        graph.state = result.x
        print_graph_summary('Final:')

    print '\n'
    print '====================='
    print '  Optimization'
    print '====================='

    optimize_graph()

    print ' '
    for constraint in (c for c in graph.constraints if isinstance(c, HomographyConstraint)):
        if constraint.enode.tag.endswith("/pose0"):
            rmse = np.sqrt(constraint.sq_unweighted_reprojection_errors().mean())
            print '  %s rmse: %.2f' % (constraint.enode.tag, rmse)

    #
    # Write out the refined intrinsics and extrinsics
    #
    homography_constraints = ( c for c in graph.constraints if isinstance(c, HomographyConstraint) )
    for constraint in homography_constraints:
        etag, itag = constraint.enode.tag, constraint.inode.tag
        K = constraint.inode.to_matrix()
        E = constraint.enode.to_matrix()
        filename = '%s/%s.lh0+' % (folder, etag)
        with open(filename, 'w') as f:
            pickle.dump((K, E), f)

if __name__ == '__main__':
    main()
