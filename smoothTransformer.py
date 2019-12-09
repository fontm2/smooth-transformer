import tensorflow as tf
from keras import backend as K
from keras import initializers, regularizers, constraints
from keras.engine import Layer, InputSpec

from ipdb import set_trace as bp

class smoothTransformer3D(Layer):
    def __init__(self, maxgrad=2, **kwargs):
        self.maxgrad = maxgrad
        super(smoothTransformer3D, self).__init__(**kwargs)

    def _integral3DImage(self, x):
        x_s = K.cumsum(x[..., 0], axis=1)
        y_s = K.cumsum(x[..., 1], axis=2)
        z_s = K.cumsum(x[..., 2], axis=3)
        out = K.stack([x_s, y_s, z_s], axis=-1)
        return out

    def _logisticGrowth(self,x):
        c = self.maxgrad
        return c / (1 + (c-1)*K.exp(-x))

    def _repeat(self, x, n_repeats):
        rep = tf.expand_dims(tf.ones(n_repeats, tf.int32), 0)
        x = tf.tensordot(tf.reshape(x, [-1, 1]), rep, axes=1)
        return tf.reshape(x, [-1]) #flatten

    def call(self, x, mask=None):
        if len(x) == 3:
            [im, defgrad, affine] = x
        else:
            [im, defgrad] = x
            
        # This function (f) enforces values to be positive and to range from [0 - maxgrad] with f(0) = 1
        defgrad = self._logisticGrowth(defgrad) 
        # This function applies an integration along the dimensions of the deformation
        sampling_grid = self._integral3DImage(defgrad) 

        # constants
        samples = tf.shape(im)[0]
        x_dim = tf.shape(im)[1]
        y_dim = tf.shape(im)[2]
        z_dim = tf.shape(im)[3]
        channels = tf.shape(im)[4]

        try:
            # adding identity to the affine gradients
            identity = tf.tile(tf.constant([[1,0,0,0,0,1,0,0,0,0,1,0]], shape=[1,12], dtype='float32'), (samples,1))
            affine = tf.reshape(affine, (-1, 12)) + identity
            affine = tf.reshape(affine, (samples, 3, 4))
            sampling_grid = tf.concat((sampling_grid, tf.ones((samples, x_dim, y_dim, z_dim, 1))), -1)
            # applying affine to the deformable part
            sampling_grid = tf.matmul(tf.reshape(sampling_grid, (samples, -1, 4)), affine, transpose_b=True)
            sampling_grid = tf.reshape(sampling_grid, (samples, x_dim, y_dim, z_dim, 3))
        except:
            pass

        x_s, y_s, z_s = sampling_grid[..., 0], sampling_grid[..., 1], sampling_grid[..., 2]

        x = tf.reshape(x_s, [-1]) #flatten
        y = tf.reshape(y_s, [-1]) #flatten
        z = tf.reshape(z_s, [-1]) #flatten

        x_dim_f = tf.cast(x_dim, K.floatx())
        y_dim_f = tf.cast(y_dim, K.floatx())
        z_dim_f = tf.cast(z_dim, K.floatx())
        out_x_dim = tf.cast(x_dim_f, 'int32')
        out_y_dim = tf.cast(y_dim_f, 'int32')
        out_z_dim = tf.cast(z_dim_f, 'int32')
        zero = tf.zeros([], dtype='int32')
        max_x = tf.cast(x_dim - 1, 'int32')
        max_y = tf.cast(y_dim - 1, 'int32')
        max_z = tf.cast(z_dim - 1, 'int32')

        # do sampling, pixels on a grid
        x0 = tf.cast(tf.floor(x), 'int32')
        x1 = x0 + 1
        y0 = tf.cast(tf.floor(y), 'int32')
        y1 = y0 + 1
        z0 = tf.cast(tf.floor(z), 'int32')
        z1 = z0 + 1

        x0 = tf.clip_by_value(x0, zero, max_x)
        x1 = tf.clip_by_value(x1, zero, max_x)
        y0 = tf.clip_by_value(y0, zero, max_y)
        y1 = tf.clip_by_value(y1, zero, max_y)
        z0 = tf.clip_by_value(z0, zero, max_z)
        z1 = tf.clip_by_value(z1, zero, max_z)

        dim3 = z_dim
        dim2 = z_dim*y_dim
        dim1 = x_dim*y_dim*z_dim
        
        base = self._repeat(tf.range(samples)*dim1, out_x_dim*out_y_dim*out_z_dim)
        idx_a = base + x0*dim2 + y0*dim3 + z0
        idx_b = base + x0*dim2 + y0*dim3 + z1
        idx_c = base + x0*dim2 + y1*dim3 + z0
        idx_d = base + x0*dim2 + y1*dim3 + z1
        idx_e = base + x1*dim2 + y0*dim3 + z0
        idx_f = base + x1*dim2 + y0*dim3 + z1
        idx_g = base + x1*dim2 + y1*dim3 + z0
        idx_h = base + x1*dim2 + y1*dim3 + z1

        # use indices to lookup pixels in the flat
        # image and restore channels dim
        im_flat = tf.reshape(im, [-1, channels])
        Ia = tf.gather_nd(im_flat, tf.expand_dims(idx_a, 1)) # 000
        Ib = tf.gather_nd(im_flat, tf.expand_dims(idx_b, 1)) # 001
        Ic = tf.gather_nd(im_flat, tf.expand_dims(idx_c, 1)) # 010
        Id = tf.gather_nd(im_flat, tf.expand_dims(idx_d, 1)) # 011
        Ie = tf.gather_nd(im_flat, tf.expand_dims(idx_e, 1)) # 100
        If = tf.gather_nd(im_flat, tf.expand_dims(idx_f, 1)) # 101
        Ig = tf.gather_nd(im_flat, tf.expand_dims(idx_g, 1)) # 110
        Ih = tf.gather_nd(im_flat, tf.expand_dims(idx_h, 1)) # 111

        # and finanly calculate trilinear interpolation
        # https://en.wikipedia.org/wiki/Trilinear_interpolation
        x0_f = tf.cast(x0, K.floatx())
        x1_f = tf.cast(x1, K.floatx())
        y0_f = tf.cast(y0, K.floatx())
        y1_f = tf.cast(y1, K.floatx())
        z0_f = tf.cast(z0, K.floatx())
        z1_f = tf.cast(z1, K.floatx())

        xd = tf.expand_dims(x-x0_f, 1)
        yd = tf.expand_dims(y-y0_f, 1)
        zd = tf.expand_dims(z-z0_f, 1)

        Cae = Ia*(1-xd) + Ie*xd
        Cbf = Ib*(1-xd) + If*xd
        Ccg = Ic*(1-xd) + Ig*xd
        Cdh = Id*(1-xd) + Ih*xd

        Caecg = Cae*(1-yd) + Ccg*yd
        Cbfdh = Cbf*(1-yd) + Cdh*yd

        output = Caecg*(1-zd) + Cbfdh*zd

        output = tf.reshape(output, [samples, x_dim, y_dim, z_dim, channels])

        return [K.cast(output, K.floatx()), sampling_grid]


class smoothTransformer2D(Layer):
    def __init__(self, maxgrad=2, **kwargs):
        self.maxgrad = maxgrad
        super(smoothTransformer2D, self).__init__(**kwargs)

    def _integralImage(self, x):
        x_s = K.cumsum(x[..., 0], axis=2)
        y_s = K.cumsum(x[..., 1], axis=1)
        out = K.stack([x_s, y_s], axis=-1)
        return out

    def _logisticGrowth(self,x):
        c = self.maxgrad
        return c / (1 + (c-1)*K.exp(-x))

    def _repeat(self, x, n_repeats):
        rep = tf.expand_dims(tf.ones(n_repeats, tf.int32), 0)
        x = tf.tensordot(tf.reshape(x, [-1, 1]), rep, axes=1)
        return tf.reshape(x, [-1]) #flatten
        

    def call(self, x, mask=None):
        if len(x) == 3:
            [im, defgrad, affine] = x
        else:
            [im, defgrad] = x
            
        # This function (f) enforces values to be positive and to range from [0 - maxgrad] with f(0) = 1
        defgrad = self._logisticGrowth(defgrad) 
        # This function applies an integration along the dimensions of the deformation
        sampling_grid = self._integralImage(defgrad) 

        # constants
        samples = tf.shape(im)[0]
        height = tf.shape(im)[1]
        width = tf.shape(im)[2]
        channels = tf.shape(im)[3]

        try:
            # apply affine transformation
            identity = tf.tile(tf.constant([[1,0,0,0,1,0,0,0,1]], shape=[1,9], dtype='float32'), (samples,1))
            affine = tf.reshape(affine, (-1, 9)) + identity
            affine = tf.reshape(affine, (samples, 3, 3))
            sampling_grid = tf.concat((sampling_grid, tf.ones((samples, height, width, 1))), -1)
            sampling_grid = tf.matmul(tf.reshape(sampling_grid, (samples, -1, 3)), affine, transpose_b=True)
            sampling_grid = tf.reshape(sampling_grid, (samples, height, width, 3))
        except:
            pass

        x_s, y_s = sampling_grid[:, :, :, 0], sampling_grid[:, :, :, 1]

        x = tf.reshape(x_s, [-1]) #flatten
        y = tf.reshape(y_s, [-1]) #flatten

        height_f = tf.cast(height, K.floatx())
        width_f = tf.cast(width, K.floatx())
        out_height = tf.cast(height_f, 'int32')
        out_width = tf.cast(width_f, 'int32')
        zero = tf.zeros([], dtype='int32')
        max_y = tf.cast(im.shape[1] - 1, 'int32')
        max_x = tf.cast(im.shape[2] - 1, 'int32')

        # do sampling
        x0 = tf.cast(tf.floor(x), 'int32')
        x1 = x0 + 1
        y0 = tf.cast(tf.floor(y), 'int32')
        y1 = y0 + 1
        
        x0 = tf.clip_by_value(x0, zero, max_x)
        x1 = tf.clip_by_value(x1, zero, max_x)
        y0 = tf.clip_by_value(y0, zero, max_y)
        y1 = tf.clip_by_value(y1, zero, max_y)
        
        dim2 = width
        dim1 = width*height
        base = self._repeat(tf.range(samples)*dim1, out_height*out_width)
        
        base_y0 = base + y0*dim2
        base_y1 = base + y1*dim2
        idx_a = base_y0 + x0
        idx_b = base_y1 + x0
        idx_c = base_y0 + x1
        idx_d = base_y1 + x1

        # use indices to lookup pixels in the flat
        # image and restore channels dim
        im_flat = tf.reshape(im, [-1, channels])
        Ia = tf.gather_nd(im_flat, tf.expand_dims(idx_a, 1))
        Ib = tf.gather_nd(im_flat, tf.expand_dims(idx_b, 1))
        Ic = tf.gather_nd(im_flat, tf.expand_dims(idx_c, 1))
        Id = tf.gather_nd(im_flat, tf.expand_dims(idx_d, 1))

        # and finanly calculate interpolated values
        x0_f = tf.cast(x0, K.floatx())
        x1_f = tf.cast(x1, K.floatx())
        y0_f = tf.cast(y0, K.floatx())
        y1_f = tf.cast(y1, K.floatx())

        wa = tf.expand_dims((x1_f-x) * (y1_f-y), 1)
        wb = tf.expand_dims((x1_f-x) * (y-y0_f), 1)
        wc = tf.expand_dims((x-x0_f) * (y1_f-y), 1)
        wd = tf.expand_dims((x-x0_f) * (y-y0_f), 1)

        output = K.sum([wa*Ia, wb*Ib, wc*Ic, wd*Id], axis=0)

        output = tf.reshape(output, (samples, height, width, channels))

        return [K.cast(output, K.floatx()), sampling_grid]