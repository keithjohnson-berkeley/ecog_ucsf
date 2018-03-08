#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quick-n-dirty loading of UCSF ecog data.
"""

# Authors: Ronald L. Sprouse (ronald@berkeley.edu)
# 
# Copyright (c) 2018, The Regents of the University of California
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
# 
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# 
# * Neither the name of the University of California nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from __future__ import division
import numpy as np
import pandas as pd
from scipy.io import loadmat
import ecog_ucsf.cmu_sphinx.htkmfc as htkmfc
import os

class ECBlock(object):
    '''An object representing a block of UCSF ECOG data.'''
    def __init__(self, basedir='', subdir='ecogDS', data=np.array([]),
htkrate=np.nan, badchan=[]):
        super(ECBlock, self).__init__()
        self.basedir = basedir
        self.subdir = subdir
        self.data = data   # Channel data for block
        self.htkrate = htkrate       # Sample rate of channel data from .htk file
        self.badchan = badchan        # List of bad channels in block

    def __repr__(self):
        r = "ECBlock(basedir='{:}', subdir='{:}', data={:}, htkrate={:}, badchan={:})".format(
            self.basedir, self.subdir, self.data, self.htkrate, self.badchan
        )
        return r

def int2wavname(n):
    '''Convert an integer in the range 1 to 256 to the ECOG file naming
convention where channel 1 is '11' and Channel 256 is '464'.'''
    return "Wav{:d}{:d}.htk".format(
        int(np.ceil(n/64)),
        int(np.mod(n-1, 64) + 1)
    )

def get_bad_channels(basedir, subdir, fname):
    '''Return an array of bad channel numbers in basedir.'''
    with open(os.path.join(basedir, subdir, fname)) as f:
        return [int(n) for n in f.readline().strip().split()]

def get_bad_segments(basedir, subdir, fname):
    '''Return a dataframe of bad segments.'''
    segfile = os.path.join(basedir, subdir, fname)
    if segfile.endswith('.mat'):
        m = loadmat(segfile)
        segs = pd.DataFrame(
            m['badTimeSegments'][:, [0,1]],
            columns=['t1', 't2']
        ).sort_values('t1')
    else:
        segs = pd.read_csv(
            segfile,
            sep=' ',
            header=None,
            usecols=[1,2],
            names=['secs', 'edge'],
            converters={
                # Convert to seconds.
                'secs': lambda x: int(x) * 1E-8,
                # Relabel 'b'->'t1' and 'e'->'t2'.
                'edge': lambda x: 't1' if x == 'b' else 't2'
            }
        )
        segs = segs.assign(segidx=np.repeat(np.arange(len(segs) // 2), 2)) \
                   .pivot_table(values='secs', index='segidx', columns='edge') \
                   .reset_index() \
                   .rename_axis(None, axis='columns') \
                   .loc[:, ['t1', 't2']]
    return segs

def replace_bad_segs(data, datarate, badsegs, val=np.nan):
    '''Given an input ndarray, return a modified ndarray in which values that occur
during the bad segment times are replaced with specified value (default np.nan).

Parameters:
data: input ndarray (n-dimensional data supported)
datarate: sample rate of the data ndarray
badsegs: bad segment dataframe as created by read_block(), i.e. columns 't1' and 't2'
val: replacement value for bad segment samples (default np.nan)

Returns:
replacement ndarray
'''
    # Convert segment times to a mask of sample indexes.
    segidx = (badsegs * datarate).apply(np.around).astype(np.int)
    segmask = np.concatenate(
        [np.arange(r.t1, r.t2) for r in segidx.itertuples()]
    )
    # Assume time is the first axis.
    data[segmask] = val
    return data

def read_block(basedir, subdir='ecogDS', channel_cb=None, dtype=np.float32,
replace=True, artifact_dir='Artifacts', badchan='badChannels.txt',
badsegs='badTimeSegments.mat'):
    '''Load all the Wav*.htk channel data in a block subdir into an ECBlock.

The channel_cb parameter may contain a callback function to apply to each
channel's data before storing in the data ndarray. The callback is applied
separately to each channel and may change the shape of the channel data,
as long as the channel shapes remain compatible with one another. If the
callback function changes the effective sample rate of the data, note that
the change will not be reflected in the rate attribute of the ECBlock
returned by read_block().

Returns an ECBlock object.
'''
    b = ECBlock(basedir=basedir)

    # Electrodes (channels) are numbered starting with 1.
    b.badchan = get_bad_channels(basedir, artifact_dir, badchan)
    b.badsegs = get_bad_segments(basedir, artifact_dir, badsegs)
    htk = htkmfc.openhtk(os.path.join(basedir, subdir, int2wavname(1)))
    b.htkrate = htk.sampPeriod * 1E-4
    c = np.squeeze(htk.getall().astype(dtype))
    if replace is True:
        c = replace_bad_segs(c, b.htkrate, b.badsegs)
    if channel_cb is not None:
        c = channel_cb(c)
    b.data = np.empty([256] + list(c.shape), dtype=dtype) * np.nan
    if (replace is False) or (1 not in b.badchan):
        b.data[0,] = c
    for idx in range(2, 257):
        if (replace is False) or (idx not in b.badchan):
            htk = htkmfc.openhtk(
                os.path.join(basedir, subdir, int2wavname(idx))
            )
            c = np.squeeze(htk.getall().astype(dtype))
            if replace is True:
                c = replace_bad_segs(c, b.htkrate, b.badsegs)
            if channel_cb is None:
                b.data[idx-1,] = c
            else:
                b.data[idx-1,] = channel_cb(c)
    return b
