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

import math
import logging
from collections import namedtuple
import h5py
import numpy as np

from strainge import kmertools, kmerizer

logger = logging.getLogger(__name__)


class Sample(kmertools.KmerSet):
    """
    Sample Kmerset
    Initially loaded with full KmerSet from the sample hdf5 file, but it will
    be reduced to kmers in the pan genome set.
    """

    def __init__(self, hdf5file):
        super().__init__()
        self.name = kmertools.name_from_path(hdf5file)

        logger.info("Loading sample %s", hdf5file)
        self.hdf5file = hdf5file
        with h5py.File(hdf5file, 'r') as h5:
            self.load_hdf5(h5)

        # keep track of original totalKmers and distinctKmers
        self.total_kmers = self.counts.sum()
        self.distinct_kmers = self.kmers.size

        logger.info("%d distinct k-mers, %d total k-mers",
                    self.distinct_kmers, self.total_kmers)


class PanGenome(kmertools.KmerSet):
    def __init__(self, hdf5file, use_fingerprint=False):
        """
        PanGenome Kmerset
        Operations on on a pan genome kmer data file (as generated by the
        `strainge createdb` utility). The pan genome kmer file has a top
        level KmerSet as well as a group containing a KmerSet for each strain
         in the pan genome.
        """

        super().__init__()
        self.hdf5file = hdf5file

        logger.info("Loading pan genome %s", hdf5file)
        self.h5 = h5py.File(hdf5file, 'r')
        self.load_hdf5(self.h5)

        self.use_fingerprint = use_fingerprint
        if self.use_fingerprint:
            self.fingerprint_override()
            logger.info("fingerprint_fraction=%f", self.fingerprint_fraction)

        # Strains are groups within hdf5 file
        self.strain_names = [name for name in list(self.h5.keys())
                             if isinstance(self.h5[name], h5py.Group)]

        logger.info("%d strains, %d distinct k-mers in pan-genome.",
                    len(self.strain_names), self.kmers.size)

        # we'll use this as a lookup for strain-specific KmerSets (filled
        # out on first iteration)
        self.strainCache = {}

    def load_strain(self, name):
        """
        Load a strain KmerSet. If it's already in our cache, use that,
        else load from hdf5 file.

        :param name: name of strain (name of group in hdf5)
        :return: Kmerset for the given strain
        :rtype: StrainKmerSet
        """

        if name in self.strainCache:
            return self.strainCache[name]
        else:
            strain = StrainKmerSet(self, name)
            self.strainCache[name] = strain
            return strain


class StrainKmerSet(kmertools.KmerSet):
    def __init__(self, pan, name):
        """
        Strain KmerSet object
        Initially contains full strain Kmset, but that might be reduced by
        excluded kmers.

        :param pan: PanGenome object
        :param name: strain name
        """
        super().__init__()
        self.name = name
        self.pan = pan
        self.load_hdf5(pan.h5[name])

        if self.pan.use_fingerprint:
            self.fingerprint_override()
        self.distinct_kmers = self.kmers.size
        self.total_kmers = self.counts.sum()


class StrainGSTResult:
    def __init__(self, pan_kmers, pan_kcov, pan_pct):
        self.pan_kmers = pan_kmers
        self.pan_kcov = pan_kcov
        self.pan_pct = pan_pct

        self.strains = []


Strain = namedtuple('Strain', [
    'strain', 'gkmers', 'ikmers', 'skmers', 'cov', 'kcov', 'gcov',
    'acct', 'even', 'wcov', 'spec', 'score0', 'score'
])


class StrainGST:
    def __init__(self, pangenome, use_fingerprint, iterations, top,
                 min_score, min_evenness, universal, min_frac, min_acct, debug_hdf5=None):
        self.use_fingerprint = use_fingerprint
        self.iterations = iterations
        self.top = top

        self.min_score = min_score
        self.min_evenness = min_evenness
        self.min_frac = min_frac
        self.min_acct = min_acct
        self.universal = universal

        self.pangenome = pangenome
        self.debug_hdf5 = debug_hdf5

    def find_close_references(self, sample, score_strains=None):
        """
        Find the strains in a sample
        :param sample: Sample object to score
        :return: Object with all results
        :rtype: StrainGSTResult
        """

        # Score all pangenome strains unless list given
        strains = score_strains or self.pangenome.strain_names


        logger.info("Sample %s has %d k-mers", sample.name, sample.counts.sum())


        # Reduce the sample KmerSet to its intersection with the PanGenome
        # to free up memory and speed things up.
        s = sample.intersect(self.pangenome.kmers)
        sample.kmers = s.kmers
        sample.counts = s.counts

        # Excludes will contain kmers removed from consideration because they
        # are too common or they were in a found in a previous strain. We exclude
        # and kmers occurring in the sample more than a multiple of the median pangenome
        # kmer frequency.
        universal_limit = np.median(sample.counts) * self.universal
        excludes = sample.kmers[sample.counts > universal_limit]
        sample.exclude(excludes)

        # Metrics for Sample kmers in pan genome
        sample_pan_kmers = sample.counts.sum()
        sample_pan_kcov = sample_pan_kmers / sample.kmers.size
        sample_pan_pct = sample_pan_kmers * 100.0 / sample.total_kmers

        if self.use_fingerprint:
            # really minHash fraction
            sample_pan_pct /= self.pangenome.fingerprint_fraction

        logger.info("Sample %s has %d k-mers (%d distinct) in common with pan-genome "
                    "database (%.2f%%)", sample.name, sample_pan_kmers, sample.kmers.size,
                    sample_pan_pct)

        result = StrainGSTResult(sample.kmers.size, sample_pan_kcov, sample_pan_pct)

        h5 = None
        if self.debug_hdf5:
            h5 = h5py.File(self.debug_hdf5, 'w')



        for i in range(self.iterations):
            # Output the remaining sample k-mers per iteration for debugging
            # purposes if requested.
            if h5 is not None:
                group = h5.create_group(f"iteration{i}")
                group.create_dataset("kmers", data=sample.kmers,
                                     compression="gzip")
                group.create_dataset("counts", data=sample.counts,
                                     compression="gzip")

            iter = map(
                lambda strain: self.score_strain(strain, sample, excludes),
                strains
            )

            strain_scores = list(
                s for s in iter if s is not None
                and s.even >= self.min_evenness
            )

            strain_scores.sort(key=lambda e: e.score, reverse=True)

            if not strain_scores:
                logger.info("No good strains found, quiting.")
                break

            winner = strain_scores[0]
            # if best score isn't good enough, we're done
            if winner.score < self.min_score:
                logger.info("Score %.3f for %s below min score %.3f, quiting.",
                            winner.score, winner.strain, self.min_score)
                break

            # Collect the winning strain (and additional extra high scoring
            # strains if requested)
            for t in range(min(self.top, len(strain_scores))):
                pos = str(i) if self.top == 1 else f"{i}.{t}"
                result.strains.append((pos, strain_scores[t]))

            winning_strain = self.pangenome.load_strain(winner.strain)

            logger.info("Found strain %s, score %.3f", winner.strain,
                        winner.score)

            # Exclude kmers from winning strain from sample (and from each
            # strain next iteration)
            excludes = winning_strain.kmers
            sample.exclude(excludes)

        if h5 is not None:
            h5.close()

        return result

    def score_strain(self, strain_name, sample, excludes=None):
        # This loads a cached version with possibly already several k-mers
        # removed from earlier found strains
        strain_kmerset = self.pangenome.load_strain(strain_name)

        if excludes is not None:
            strain_kmerset.exclude(excludes)

        if (strain_kmerset.kmers.size < self.min_frac *
                strain_kmerset.distinct_kmers):
            # Too few k-mers
            return None

        # how often each strain kmer occurs in PanGenome
        ix = kmerizer.intersect_ix(self.pangenome.kmers, strain_kmerset.kmers)
        strain_pan_counts = self.pangenome.counts[ix]

        # distinct kmers from sample in this strain
        kmers = kmerizer.intersect(strain_kmerset.kmers, sample.kmers)

        # if none, quit now
        if kmers.size == 0:
            return None

        # how many times each occurred in this strain
        ix = kmerizer.intersect_ix(strain_kmerset.kmers, kmers)
        counts = strain_kmerset.counts[ix]

        # how many times each strain kmer occurred in pan genome (for
        # weighting)
        pan_counts = strain_pan_counts[ix]

        # how many times did each kmers occur in sample?
        ix = kmerizer.intersect_ix(sample.kmers, kmers)
        sample_counts = sample.counts[ix]
        sample_count = sample_counts.sum()

        # converse of covered: what fraction of pan genome sample kmers are
        # accounted for by this sample?
        accounted = sample_count / sample.counts.sum()

        if accounted < self.min_acct:
            return None

        # Compute metrics
        # what fraction of the distinct strain kmers are in the sample?
        covered = kmers.size / strain_kmerset.kmers.size

        # for each distinct kmer, how many times did it occur in the sample
        # relative to the strain?
        kmer_coverage = sample_count / sample_counts.size

        # mean genome coverage from all my kmers
        genome_coverage = sample_count / strain_kmerset.counts.sum()

        # Lander-Waterman estimate of percentage covered if randomly
        # distributed across genome
        est_covered = 1.0 - math.exp(-genome_coverage)

        # measure of evenness of coverage
        evenness = covered / est_covered

        # original panstrain simple scoring metric
        score = covered * accounted * min(evenness, 1.0 / evenness)

        # Weight each of my kmers by inverse of times it occurs in pan genome
        # relative to this genome
        # strain_weights = strain_kmerset.counts * (1.0 / strain_pan_counts)
        weights = 1.0 / pan_counts
        strain_total_weight = (counts * weights).sum()

        # Weight of each sample kmer
        sample_total_weight = (sample_counts * weights).sum()

        # Weighted genome coverage
        weighted_coverage = sample_total_weight / strain_total_weight

        # Specificity is a measure of how specific the sample kmers are to
        # this strain. If they are randomly sampled, this should be close
        # to 1. A low number indicates that the sample kmers that hit this
        # strain also tend to be found in other strains. A high number
        # indicates that more kmers specific to this strain are found that
        # would be exected from random sampling, e.g., maybe the sample only
        # contains a chunk of this genome.
        strain_mean_weight = strain_total_weight / counts.sum()
        sample_mean_weight = sample_total_weight / sample_count
        specificity = sample_mean_weight / strain_mean_weight

        # add in specificity component (best match should be close to 1.0,
        # higher or lower is worse)
        weighted_score = score * min(specificity, 1.0 / specificity)

        return Strain(
            strain=strain_name,
            gkmers=strain_kmerset.distinct_kmers,
            ikmers=strain_kmerset.kmers.size,
            skmers=sample.kmers.size,
            cov=covered,
            kcov=kmer_coverage,
            gcov=genome_coverage,
            acct=accounted,
            even=evenness,
            wcov=weighted_coverage,
            spec=specificity,
            score0=score,
            score=weighted_score
        )
