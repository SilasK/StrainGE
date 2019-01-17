#  Copyright (c) 2016-2019, Broad Institute, Inc. All rights reserved.
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#
#  * Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
#  * Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
#  * Neither the name Broad Institute, Inc. nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
#  AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
#  IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
#  ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
#  LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
#  CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
#  SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
#  INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
#  CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
#  ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
#  POSSIBILITY OF SUCH DAMAGE.
#

import os
import bz2
import gzip
import logging

import h5py
import pysam
import numpy as np
from Bio import SeqIO
import matplotlib.pyplot as plt

from strainge import kmerizer

logger = logging.getLogger(__name__)

DEFAULT_K = 23
DEFAULT_FINGERPRINT_FRACTION = 0.01
OLD_FINGERPRINT_FRACTION = 0.002

A = 0
C = 1
G = 2
T = 3

BASES = "ACGT"


def kmer_string(k, kmer):
    seq = ''.join([BASES[(kmer >> (2 * k)) & 3] for k in range(k - 1, -1, -1)])
    return seq


def open_seq_file(file_name):
    """
    Open a sequence file with SeqIO; can be fasta or fastq with optional gz or bz2 compression.
    Assumes fasta unless ".fastq" or ".fq" in the file name.
    :param fileName:
    :return: SeqIO.parse object
    """

    components = file_name.split('.')

    if "bam" in components:
        file = pysam.AlignmentFile(file_name, "rb", check_header=False,
                                   check_sq=False)

        # generator for sequences in bam
        def bam_sequences():
            for read in file.fetch(until_eof=True):
                if not read.is_qcfail:
                    yield read

        return bam_sequences()

    if "bz2" in components:
        file = bz2.open(file_name, 'rt')
    elif "gz" in components:
        file = gzip.open(file_name, 'rt')
    else:
        file = open(file_name, 'r')

    if "fastq" in components or "fq" in components:
        file_type = "fastq"
    else:
        file_type = "fasta"
    return SeqIO.parse(file, file_type)


def load_hdf5(file_path, thing):
    with h5py.File(file_path, 'r') as h5:
        if h5.attrs['type'] != "KmerSet":
            raise ValueError("The HDF5 file is not a KmerSet, unexpected type:"
                             " '{}'".format(h5.attrs['type']))

        return np.array(h5[thing])


def load_kmers(file_name):
    return load_hdf5(file_name, "kmers")


def load_counts(file_name):
    return load_hdf5(file_name, "counts")


def load_fingerprint(file_name):
    return load_hdf5(file_name, "fingerprint")


def name_from_path(file_path):
    return os.path.splitext(os.path.basename(file_path))[0]


def kmerset_from_hdf5(file_path):
    if not file_path.endswith(".hdf5"):
        file_path += ".hdf5"
    with h5py.File(file_path, 'r') as h5:
        assert h5.attrs["type"] == "KmerSet", "Not a KmerSet file!"
        kset = KmerSet(h5.attrs['k'])
        if "fingerprint" in h5:
            kset.fingerprint = np.array(h5["fingerprint"])
        if "kmers" in h5:
            kset.kmers = np.array(h5["kmers"])
        if "counts" in h5:
            kset.counts = np.array(h5["counts"])
    return kset


def kmerset_from_file(file_path, k=DEFAULT_K):
    return kmerset_from_hdf5(file_path)


def similarity_score(kmers1, kmers2, scoring="jaccard"):
    """Compute Jaccard similarity index"""
    # count of kmers in common
    intersection = float(kmerizer.count_common(kmers1, kmers2))
    if scoring == "jaccard":
        # Use Jaccard similarity index
        score = intersection / (kmers1.size + kmers2.size - intersection)
    elif scoring == "minsize":
        # Use intersection / min_size (proper subset scores 1.0)
        score = intersection / min(kmers1.size, kmers2.size)
    elif scoring == "meansize":
        # Use mean size in denominator (used in Mash)
        score = intersection / ((kmers1.size + kmers2.size) / 2)
    elif scoring == "maxsize":
        # Use intersection / max_size (proper subset scores min/max)
        score = intersection / max(kmers1.size, kmers2.size)
    elif scoring == "reference":
        # Use intersection / size of reference (useful for comparing reads to
        # assembled references)
        score = intersection / kmers2.size
    else:
        assert scoring in (
            "jaccard", "minsize", "maxsize", "meansize", "reference"), \
            "unknown scoring method"
    return score


def similarity_numerator_denominator(kmers1, kmers2, scoring="jaccard"):
    """Compute Jaccard similarity index"""
    # count of kmers in common
    intersection = float(kmerizer.count_common(kmers1, kmers2))
    if scoring == "jaccard":
        # Use Jaccard similarity index
        denom = (kmers1.size + kmers2.size - intersection)
    elif scoring == "minsize":
        # Use intersection / min_size (proper subset scores 1.0)
        denom = min(kmers1.size, kmers2.size)
    elif scoring == "maxsize":
        # Use intersection / max_size (proper subset scores min/max)
        denom = max(kmers1.size, kmers2.size)
    elif scoring == "reference":
        # Use intersection / size of reference (useful for comparing reads to
        # assembled references)
        denom = kmers2.size
    else:
        assert scoring in ("jaccard", "minsize", "maxsize"), \
            "unknown scoring method"
    return intersection, denom


def build_kmer_count_matrix(kmersets):
    """Build a big matrix with kmer counts from a list of kmersets.

    Each column will represent a single k-mer set and each row a k-mer. This
    will effectively merge all kmersets to a single matrix.

    Parameters
    ----------
    kmersets : List[KmerSet]
        List of `KmerSet` objects to build the matrix from.

    Returns
    -------
    Tuple[List[kmer_t], array]
        This function returns a tuple with two elements: the first element is
        a list of k-mers, i.e. the labels for the rows of the matrix, and the
        second element is the matrix itself.
    """

    # Defer to our C++ extension
    return kmerizer.build_kmer_count_matrix([
        (kmerset.kmers, kmerset.counts) for kmerset in kmersets
    ])


class KmerSet(object):
    """
    Holds array of kmers and their associated counts & stats.
    """

    def __init__(self, k=DEFAULT_K):
        self.k = k
        # data arrays
        self.kmers = None
        self.counts = None
        self.fingerprint = None
        self.singletons = None

        # stats from kmerizing, if appropriate
        self.n_seqs = 0
        self.n_bases = 0
        self.n_kmers = 0

    def __eq__(self, other):
        return (self.k == other.k
                and np.array_equal(self.fingerprint, other.fingerprint)
                and np.array_equal(self.kmers, other.kmers)
                and np.array_equal(self.counts, other.counts))

    def kmerize_file(self, file_name, batch_size=100000000, verbose=True,
                     limit=0, prune=0):
        seq_file = open_seq_file(file_name)
        batch = np.empty(batch_size, dtype=np.uint64)

        n_seqs = 0
        n_bases = 0
        n_kmers = 0
        n_batch = 0  # kmers in this batch
        pruned = False

        for seq in seq_file:
            n_seqs += 1
            seq_length = len(seq.seq)
            n_bases += seq_length
            if n_kmers + seq_length > batch_size:
                self.process_batch(batch, n_seqs, n_bases, n_kmers, verbose)
                if limit and self.n_kmers > limit:
                    break
                if prune and self.singletons > prune:
                    self.prune_singletons(verbose)
                    pruned = True
                n_seqs = 0
                n_bases = 0
                n_kmers = 0
            n_kmers += kmerizer.kmerize_into_array(self.k, str(seq.seq), batch,
                                                   n_kmers)
            if limit and self.n_kmers + n_kmers >= limit:
                break

        seq_file.close()
        self.process_batch(batch, n_seqs, n_bases, n_kmers, verbose)
        if pruned:
            self.prune_singletons(verbose)

    def kmerize_seq(self, seq):
        kmers = kmerizer.kmerize(self.k, seq)
        self.n_seqs += 1
        self.n_bases += len(seq)
        self.n_kmers = kmers.size
        self.kmers, self.counts = np.unique(kmers, return_counts=True)

    def process_batch(self, batch, nseqs, nbases, nkmers, verbose):
        self.n_seqs += nseqs
        self.n_bases += nbases
        self.n_kmers += nkmers

        new_kmers, new_counts = np.unique(batch[:nkmers], return_counts=True)

        if self.kmers is None:
            self.kmers = new_kmers
            self.counts = new_counts
        else:
            self.kmers, self.counts = kmerizer.merge_counts(
                self.kmers, self.counts, new_kmers, new_counts)

        self.singletons = np.count_nonzero(self.counts == 1)
        if verbose:
            self.print_stats()

    def prune_singletons(self, verbose=False):
        keepers = self.counts > 1
        self.kmers = self.kmers[keepers]
        self.counts = self.counts[keepers]
        logger.debug("Pruned singletons: %d distinct k-mers remain",
                     self.kmers.size)

    def merge_kmerset(self, other):
        """Create new KmerSet by merging this with another"""
        new_set = KmerSet(self.k)
        new_set.kmers, new_set.counts = kmerizer.merge_counts(
            self.kmers, self.counts, other.kmers, other.counts)
        return new_set

    def intersect(self, kmers):
        """
        Compute intersection with given kmers
        :param kmers: kmers to keep
        :return: reduced version of self
        """

        ix = kmerizer.intersect_ix(self.kmers, kmers)
        self.counts = self.counts[ix]
        self.kmers = self.kmers[ix]

        return self

    def exclude(self, kmers):
        """
        Return this KmerSet with excluded kmers removed.
        :param kmers: kmers to exclude
        :return: reduced version of self
        """
        new_kmers = kmerizer.diff(self.kmers, kmers)

        ix = kmerizer.intersect_ix(self.kmers, new_kmers)
        self.counts = self.counts[ix]
        self.kmers = new_kmers

        return self

    def mutual_intersect(self, other):
        """
        Compute intersection of two kmer sets and reduce both to their common
        kmers. BOTH sets are modified!

        :param other: other KmerSet
        :return: reduced self
        """
        ix = kmerizer.intersect_ix(self.kmers, other.kmers)
        self.kmers = self.kmers[ix]
        self.counts = self.counts[ix]

        ix = kmerizer.intersect_ix(other.kmers, self.kmers)
        other.kmers = other.kmers[ix]
        other.counts = other.counts[ix]

        return self

    def print_stats(self):
        print('Seqs:', self.n_seqs, 'Bases:', self.n_bases, 'Kmers:',
              self.n_kmers, 'Distinct:', self.kmers.size,
              'Singletons:', self.singletons)

    def min_hash(self, frac=0.002):
        nkmers = int(round(self.kmers.size * frac))
        order = kmerizer.fnvhash_kmers(self.k, self.kmers).argsort()[:nkmers]
        self.fingerprint = self.kmers[order]
        self.fingerprint.sort()
        return self.fingerprint

    def freq_filter(self, min_freq=1, max_freq=None):
        condition = (self.counts >= min_freq)
        if max_freq:
            condition &= (self.counts <= max_freq)
        self.kmers = self.kmers[condition]
        self.counts = self.counts[condition]

    def spectrum(self):
        return np.unique(self.counts, return_counts=True)

    def spectrum_min_max(self, delta=.5, max_copy_number=20):
        freq, counts = self.spectrum()
        min_index = 0
        max_index = 0
        have_min = False
        have_max = False
        last_freq = 0
        for i in range(freq.size):
            count = counts[i]
            zero = freq[i] > last_freq + 1
            if have_max and (
                    zero or freq[i] > freq[max_index] * max_copy_number):
                break
            if have_min:
                if count > counts[max_index]:
                    max_index = i
                if count < counts[max_index] * (1 - delta):
                    have_max = True
            elif count > 1000 and count > counts[min_index] * (1 + delta):
                have_min = True
            elif zero or count < counts[min_index]:
                min_index = i
                max_index = i
            elif counts[i] < counts[min_index]:
                min_index = max_index = i
            last_freq = freq[i]
        if min_index and max_index and counts[max_index] > counts[
            min_index] * (1 + delta):
            return (freq[min_index], freq[max_index], freq[i - 1])
        return None

    def spectrum_filter(self, max_copy_number=20):
        thresholds = self.spectrum_min_max()
        if thresholds:
            self.freq_filter(thresholds[0], thresholds[2])
        return thresholds

    def plot_spectrum(self, file_name=None, max_freq=None):
        # to get kmer profile, count the counts!
        spectrum = self.spectrum()
        plt.semilogy(spectrum[0], spectrum[1])
        plt.grid = True
        if max_freq:
            plt.xlim(0, max_freq)
        plt.xlabel("Kmer Frequency")
        plt.ylabel("Number of Kmers")
        if file_name:
            plt.savefig(file_name)
        else:
            plt.show()

    def write_histogram(self, file_obj):
        spectrum = self.spectrum()
        for i in range(spectrum[0].size):
            print("%d\t%d" % (spectrum[0][i], spectrum[1][i]), file=file_obj)

    def entropy(self):
        """Calculate Shannon entropy in bases"""
        if self.counts is None:
            return 0.0
        total = float(self.counts.sum())
        probs = self.counts / total
        return (-(probs * np.log2(probs)).sum()) / 2

    def save_hdf5(self, h5, compress=None):
        h5.attrs["type"] = "KmerSet"
        h5.attrs["k"] = self.k
        h5.attrs["nSeqs"] = self.n_seqs
        if self.fingerprint is not None:
            h5.create_dataset("fingerprint", data=self.fingerprint,
                              compression=compress)
        if self.kmers is not None:
            h5.create_dataset("kmers", data=self.kmers, compression=compress)
        if self.counts is not None:
            h5.create_dataset("counts", data=self.counts, compression=compress)

    def save(self, file_name, compress=None):
        """Save in HDF5 file format"""
        if compress is True:
            compress = "gzip"
        if not file_name.endswith(".hdf5"):
            file_name += ".hdf5"
        with h5py.File(file_name, 'w') as h5:
            self.save_hdf5(h5, compress)

    def load_hdf5(self, h5):
        if h5.attrs['type'] != "KmerSet":
            raise ValueError("The HDF5 file is not a KmerSet, unexpected type:"
                             " '{}'".format(h5.attrs['type']))
        self.k = int(h5.attrs['k'])
        if 'nSeqs' in h5.attrs:
            self.n_seqs = int(h5.attrs['nSeqs'])

        if "fingerprint" in h5:
            self.fingerprint = np.array(h5["fingerprint"])
        if "kmers" in h5:
            self.kmers = np.array(h5["kmers"])
        if "counts" in h5:
            self.counts = np.array(h5["counts"])

    def load(self, file_name):
        with h5py.File(file_name, 'r') as h5:
            self.load_hdf5(h5)
