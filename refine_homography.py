from math import sqrt
import numpy as np
from scipy.optimize import root, minimize




class IntrinsicsEstimationError(RuntimeError):
    pass


def _check_homographies(homographies, nmin):
    assert all([ h.shape == (3,3)  for h in homographies ])

    if len(homographies) < nmin:
        raise IntrinsicsEstimationError(
                'Need at least %d homographies to estimate intrinsics' % nmin )


def _coeff_vec(H , i, j):
    """
    Helper function to compute the coefficient vector for
    homography constraints
    """
    a0, a1, a2 = H[:,i]
    b0, b1, b2 = H[:,j]
    return np.array([
                (a0*b0),
                (a0*b1 + a1*b0),
                (a1*b1),
                (a2*b0 + a0*b2),
                (a2*b1 + a1*b2),
                (a2*b2)
            ])


def _cam_matrix_from_B(b11, b12, b22, b13, b23, b33):
    """
    Given the matrix B describing the image of the absolute
    conic, compute the intrinsics matrix K. We know that:

                B = K.inv().T * K.inv()
    """
    # 1-based indices. Yuck! But code is consistent with the
    # formulas in the paper.
    v0      =  (b12*b13 - b11*b23) / (b11*b22 - b12*b12)
    lambda_ =  b33 - (b13*b13 + v0*(b12*b13 - b11*b23)) / b11
    alpha   =  sqrt(lambda_ / b11)
    beta    =  sqrt(lambda_*b11 / (b11*b22 - b12*b12))
    gamma   =  -b12*alpha*alpha*beta / lambda_
    u0      =  gamma*v0 / beta - b13*alpha*alpha / lambda_

    return np.array([[ alpha, gamma,   u0 ],
                     [     0,  beta,   v0 ],
                     [     0,     0,    1 ]])


def estimate_intrinsics(homographies):
    """
    Estimate camera intrinsics `K` from a list of `homographies`. The
    method uses constraints imposed by the image of the absolute conic,
    as described in:
        Z. Zhang, "A flexible new technique for camera calibration",
        Section 3.1,
        IEEE Transactions on Pattern Analysis and Machine Intelligence

    if `homographies` contains less than 3 homographies, the skew (or
    aspect ratio) of the estimated intrinsics matrix `K` is constrained
    to be close to zero.
    """
    _check_homographies(homographies, nmin=2)

    constraints = []
    for H in homographies:
        constraints.append(_coeff_vec(H,0,1))
        constraints.append(_coeff_vec(H,0,0) - _coeff_vec(H,1,1))

    if len(homographies) == 2: # constrain skew = 0
        constraints.append(np.array([0, 1, 0, 0, 0, 0]))

    C = np.vstack(constraints)
    U, s, Vt = np.linalg.svd(C)

    b11, b12, b22, b13, b23, b33 = Vt.T[:,-1]
    return _cam_matrix_from_B(b11, b12, b22, b13, b23, b33)


def estimate_intrinsics_noskew(homographies):
    """
    Similar to `estimate_intrinsics`, but assumes the skew of the
    camera `gamma` is zero.
    """
    _check_homographies(homographies, nmin=2)

    constraints = []
    for H in homographies:
        constraints.append(_coeff_vec(H,0,1))
        constraints.append(_coeff_vec(H,0,0) - _coeff_vec(H,1,1))

    C = np.vstack(constraints)
    # delete column corresponding to b12, since we know b12 == 0
    C = np.delete(C, 1, axis=1)
    U, s, Vt = np.linalg.svd(C)

    b11, b22, b13, b23, b33 = Vt.T[:,-1]
    b12 = 0.
    return _cam_matrix_from_B(b11, b12, b22, b13, b23, b33)


def estimate_intrinsics_noskew_assume_cxy(homographies, cxy):
    """
    Similar to `estimate_intrinsics`, but assumes that the camera
    center `cxy` is known, and the skew of the camera `gamma` is zero.
    These assumptions allow this method to estimate camera intrinsics
    from just one input homography.
    """
    _check_homographies(homographies, nmin=1)

    u0, v0 = cxy

    constraints = []
    for H in homographies:
        h00, h10, h20 = H[:,0]
        h01, h11, h21 = H[:,1]

        constraints.append([
            h00*h01 - u0*h00*h21 - u0*h01*h20 + u0*u0*h20*h21,
            h10*h11 - v0*h10*h21 - v0*h11*h20 + v0*v0*h20*h21,
            h20*h21
            ])
        constraints.append([
            h00*h00 - h01*h01 - 2*u0*h00*h20 + 2*u0*h01*h21 + u0*u0*h20*h20 - u0*u0*h21*h21,
            h10*h10 - h11*h11 - 2*v0*h10*h20 + 2*v0*h11*h21 + v0*v0*h20*h20 - v0*v0*h21*h21,
            h20*h20 - h21*h21
            ])

    C = np.vstack(constraints)
    U, s, Vt = np.linalg.svd(C)
    alpha, beta, _ = np.sqrt(1./Vt.T[:,-1])

    return np.array([[ alpha,    0.,   u0 ],
                     [     0,  beta,   v0 ],
                     [     0,     0,    1 ]])


def get_extrinsics_from_homography(H, intrinsics):
    """
    Ideally `E = K.inv * H`, where `E` is the extrinsics and
    `K` is the camera intrinsics. However the resulting `E`
    does not conform to a rigid body transform. Hence, we
    correct `E` to conform to a rigid body transform.
    """
    M = np.linalg.inv(intrinsics).dot(H)
    M0 = M[:,0]
    M1 = M[:,1]

    # Columns should be unit vectors
    M0_scale = np.linalg.norm(M0)
    M1_scale = np.linalg.norm(M1)
    scale = sqrt(M0_scale) * sqrt(M1_scale)

    M /= scale
    # Recover sign of scale factor by noting that observations
    # must be in front of the camera, that is: z < 0
    if M[1,2] > 0: M *= -1

    # Assemble extrinsics matrix from the columns of M
    E = np.eye(4)
    E[:3,0] = M0
    E[:3,1] = M1
    E[:3,2] = np.cross(M0, M1)
    E[:3,3] = M[:,2]

    # Ensure that the rotation part of `E` is ortho-normal
    # For this we use the polar decomposition to find the
    # closest ortho-normal matrix to `E[:3,:3]`
    U, s, Vt = np.linalg.svd(E[:3,:3])
    E[:3,:3] = U.dot(Vt)

    return E


def _xyzrph_to_matrix(x, y, z, r, p, h):
    from numpy import cos, sin

    rotx = lambda t: \
        np.array([[  1.,      0.,      0.,  0. ],
                  [  0.,  cos(t), -sin(t),  0. ],
                  [  0.,  sin(t),  cos(t),  0. ],
                  [  0.,      0.,      0.,  1. ]])

    roty = lambda t: \
        np.array([[  cos(t),  0.,  sin(t),  0. ],
                  [    0.,    1.,    0.,    0. ],
                  [ -sin(t),  0.,  cos(t),  0. ],
                  [    0.,    0.,    0.,    1. ]])

    rotz = lambda t: \
        np.array([[ cos(t), -sin(t),  0.,   0. ],
                  [ sin(t),  cos(t),  0.,   0. ],
                  [     0.,      0.,  1.,   0. ],
                  [     0.,      0.,  0.,   1. ]])

    translate = lambda x, y, z: \
        np.array([[ 1.,  0.,  0.,  x  ],
                  [ 0.,  1.,  0.,  y  ],
                  [ 0.,  0.,  1.,  z  ],
                  [ 0.,  0.,  0.,  1. ]])

    return reduce(np.dot,
        [ translate(x, y, z), rotz(h), roty(p), rotx(r) ])


def _matrix_to_xyzrph(M):
    tx = M[0,3]
    ty = M[1,3]
    tz = M[2,3]
    rx = np.arctan2(M[2,1], M[2,2])
    ry = np.arctan2(-M[2,0], sqrt(M[0,0]*M[0,0] + M[1,0]*M[1,0]))
    rz = np.arctan2(M[1,0], M[0,0])
    return tx, ty, tz, rx, ry, rz


def _intrisics_to_matrix(fx, fy, cx, cy):
    return np.array([[ fx,   0,  cx,  0. ],
                     [  0,  fy,  cy,  0. ],
                     [  0,   0,   1,  0. ]])


def _matrix_to_intrinsics(K):
    return K[0,0], K[1,1], K[0,2], K[1,2]


def _reprojection_error(fx, fy, x, y, z, r, p, h, cx, cy, p_src, p_tgt, weights=None):
    """
    compute the mean geometric reprojection error of the world
    points `p_src` through this homography.
    """
    K = _intrisics_to_matrix(fx, fy, cx, cy)
    E = _xyzrph_to_matrix(x, y, z, r, p, h)
    H = K.dot(E)

    # convert to 3-D homogeneous form with z=0
    N = len(p_src)
    p_src = np.hstack([ p_src, np.zeros((N,1)), np.ones((N,1)) ])
    p_src = p_src.T

    p_mapped = H.dot(p_src)[:3,:]

    # normalize homogeneous coordinates
    M = np.diag(1./p_mapped[2,:])
    p_mapped = p_mapped[:2,:].dot(M)

    if weights == None:
        weights = np.ones((N,))

    W = np.diag(weights)
    p_tgt = np.transpose(p_tgt)
    sqerr = ((p_mapped - p_tgt)**2)
    #print '   [ refine_homography ] rmse: %.4f' % sqrt(sqerr.dot(W).sum(axis=0).mean())
    return sqerr.dot(W).ravel()


def refine_homography(H0, cxy, p_src, p_tgt, weights=None):
    """
    Refine the homography H by minimizing reprojection error
    using non-linear optimization
    """
    K0 = estimate_intrinsics_assume_cxy_noskew([H0], cxy)
    E0 = get_extrinsics_from_homography(H0, K0)

    fx, fy, cx, cy = _matrix_to_intrinsics(K0)
    x, y, z, r, p, h = _matrix_to_xyzrph(E0)

    #objective = lambda theta, args: \
    def objective(theta, *args):
        v = tuple(theta) + args
        return _reprojection_error(*v)

    x0 = fx, fy, x, y, z, r, p, h
    args = cx, cy, p_src, p_tgt, weights
    result = root(objective, x0, args, method='lm')
    # print result

    fx, fy, x, y, z, r, p, h = result.x

    K = _intrisics_to_matrix(fx, fy, cx, cy)
    E = _xyzrph_to_matrix(x, y, z, r, p, h)
    H = K.dot(E)

    # print ''
    # print 'H =\n', H0
    # print H
    # print '\nK =\n', K0
    # print K
    # print '\nE =\n', E0
    # print E
    # print ''

    return H


def main():
    from skimage.io import imread
    from skimage.color import rgb2gray
    from apriltag import AprilTagDetector
    from tag36h11_mosaic import TagMosaic

    np.set_printoptions(precision=4, suppress=True)

    filename = "/var/tmp/capture/38.png"
    print '\n========================================'
    print '  File: ' + filename
    print '========================================\n'

    im = imread(filename)
    im = rgb2gray(im)
    im = (im * 255.).astype(np.uint8)

    tag_mosaic = TagMosaic(0.0254)
    detections = AprilTagDetector().detect(im)
    print '  %d tags detected.' % len(detections)

    #
    # Sort detections by distance to center
    #
    c_i = np.array([im.shape[1], im.shape[0]]) / 2.
    dist = lambda p_i: np.linalg.norm(p_i - c_i)
    closer_to_center = lambda d1, d2: int(dist(d1.c) - dist(d2.c))
    detections.sort(cmp=closer_to_center)

    mosaic_pos = lambda det: tag_mosaic.get_position_meters(det.id)

    det_i = np.array([ d.c for d in detections ])
    det_w = np.array([ mosaic_pos(d) for d in detections ])

    H = np.array([[ 4828.4188,    72.3635,  -274.7845 ],
                  [  -52.191,  -4826.676,    -25.5858 ],
                  [   -0.0391,     0.05,       0.5939 ]])

    print refine_homography(H, c_i, det_w[:4], det_i[:4])

if __name__ == '__main__':
    main()