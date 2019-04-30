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

import csv
import sys
import logging
import argparse

from strainge.search_tool import StrainGST, PanGenome, Sample
from strainge.cli.registry import Subcommand

logger = logging.getLogger()

sample_stats_tsv_columns = dict([
    ("sample", "%s"),
    ("totalkmers", "%d"),
    ("distinct", "%d"),
    ("pkmers", "%d"),
    ("pkcov", "%.3f"),
    ("pan%", "%.3f")
])

strain_tsv_columns = dict([
    ("i", "%s"),
    ("strain", "%s"),
    ("gkmers", "%d"),
    ("ikmers", "%d"),
    ("skmers", "%d"),
    ("cov", "%.3f"),
    ("kcov", "%.3f"),
    ("gcov", "%.3f"),
    ("acct", "%.3f"),
    ("even", "%.3f"),
    ("score0", "%.3f"),
    ("spec", "%.3f"),
    ("wcov", "%.3f"),
    ("score", "%.3f"),
])


class StrainGSTSubCommand(Subcommand):
    """
    StrainGST: strain genome search tool. Identify close reference genomes
    to strains present in a sample.
    """
    def register_arguments(self, subparser: argparse.ArgumentParser):
        subparser.add_argument(
            "-o", "--output", type=argparse.FileType('w'), default=sys.stdout,
            help="output text file (default: standard out)"
        )
        subparser.add_argument(
            "-d", "--debug-out", default="", required=False,
            help="Output a debug HDF5 file containing the remaining sample "
                 "k-mers per iteration. Optional."
        )
        subparser.add_argument(
            "-i", "--iterations", type=int, default=5,
            help="max strains to look for (default: 5)"
        )
        subparser.add_argument(
            "-t", "--top", type=int, default=1,
            help="How many best matches to print per iteration (default: %("
                 "default)d)"
        )
        subparser.add_argument(
            "-f", "--fingerprint", action='store_true',
            help="Using fingerprint rather than whole kmer set"
        )
        subparser.add_argument(
            "-F", "--minfrac", type=float, default=0.01,
            help="minimum fraction of original kmers in strain (default: %("
                 "default).2f)"
        )
        subparser.add_argument(
            "-s", "--score", type=float, default=0.01,
            help="minimum score (default: %(default).2f)"
        )
        subparser.add_argument(
            "-e", "--evenness", type=float, default=0.6,
            help="minimum evenness (default: %(default).2f)"
        )
        subparser.add_argument(
            "-S", "--score-strains", action='append',
            help="only score these strains (primarily for debugging)"
        )
        subparser.add_argument(
            "pan",
            help="hdf5 file containing pan genome kmer set"
        )
        subparser.add_argument(
            "sample",
            help="Search for strains in this sample"
        )

    def __call__(self, pan, sample, output, debug_out, iterations, top,
                 fingerprint, minfrac, score, evenness, score_strains,
                 *args, **kwargs):

        logger.info("Running StrainGST on sample %s with database %s",
                    sample, pan)
        pandb = PanGenome(pan)
        sample_kmerset = Sample(sample)

        straingst = StrainGST(pandb, fingerprint, iterations, top, score,
                              evenness, minfrac, debug_out)

        results = straingst.find_close_references(sample_kmerset,
                                                  score_strains=score_strains)

        writer = csv.writer(output, delimiter='\t', lineterminator='\n')
        writer.writerow(list(sample_stats_tsv_columns.keys()))

        # First output some sample stats
        values = {
            'sample': sample_kmerset.name,
            'totalkmers': sample_kmerset.total_kmers,
            'distinct': sample_kmerset.distinct_kmers,
            'pkmers': results.pan_kmers,
            'pkcov': results.pan_kcov,
            'pan%': results.pan_pct
        }

        # Format to string according to format
        v = [sample_stats_tsv_columns[col] % values[col]
             for col in sample_stats_tsv_columns.keys()]
        writer.writerow(v)

        # Output found strains
        writer.writerow(list(strain_tsv_columns.keys()))
        for pos, strain in results.strains:
            values = strain._asdict()
            values['i'] = pos

            v = [strain_tsv_columns[col] % values[col]
                 for col in strain_tsv_columns]
            writer.writerow(v)

        logger.info("Done.")
