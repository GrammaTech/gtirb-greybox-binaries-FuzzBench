# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Integration code for ddisasm based AFLplusplus fuzzer"""

import os
import shutil
import subprocess

from fuzzers import utils


def create_assembler():
    """
        Returns the shell script required to assemble the assembly source file
        with instrumentation.

        Returns
        -------
        text : str

    """
    text = '''#!/bin/bash
set -ex

SOURCE=$3

# Add tab before .text and substitute space indentation for tabs to allow instrumentation
sed 's/^\.text$/\\t.text/' -i $SOURCE
sed 's/^\s\\{1,\\}/\\t/' -i $SOURCE


AFL_AS_FORCE_INSTRUMENT=1 AFL_KEEP_ASSEMBLY=1 /src/afl/afl-gcc $@
'''
    return text


def build_uninstrumented_benchmark():
    """
    Block of code to build a binary without instrumentation. Takes and returns
    no values.
    """
    # Setting environment variables.
    os.environ['CC'] = 'clang'
    os.environ['CXX'] = 'clang++'
    os.environ['CFLAGS'] = ' '.join(utils.NO_SANITIZER_COMPAT_CFLAGS)
    cxxflags = [utils.LIBCPLUSPLUS_FLAG] + utils.NO_SANITIZER_COMPAT_CFLAGS
    os.environ['CXXFLAGS'] = ' '.join(cxxflags)
    os.environ['FUZZER_LIB'] = '/libStandaloneFuzzTarget.a'
    fuzzing_engine_path = '/usr/lib/libFuzzingEngine.a'
    shutil.copy(os.environ['FUZZER_LIB'], fuzzing_engine_path)
    env = os.environ.copy()

    # Build the benchmark without instrumentation.
    build_script = os.path.join(os.environ['SRC'], 'build.sh')
    subprocess.check_call(
        ['/bin/bash', '-ex', build_script],
        env=env,
    )


def instrument_binary():
    """
    Block of code to instrument a binary without source. Takes and returns no
    values.
    """
    # Name initialisation
    target_binary = os.getenv('FUZZ_TARGET')
    target_gtirb = target_binary + '.gtirb'
    instrumented_binary = target_binary + '.dafl'

    # ddisasm pipeline
    subprocess.run([
        'ddisasm',
        os.environ['OUT'] + '/' + target_binary,
        '--ir',
        target_gtirb,
    ],
                   check=True)

    assembler = '/src/fuzzers/aflplusplus_ddisasm/assemble.sh'
    with open(assembler, mode='w', encoding='utf-8') as file:
        file.write(create_assembler())
    os.chmod(assembler, 0o777)

    subprocess.run([
        'gtirb-pprinter', target_gtirb, '--syntax', 'att', '--binary',
        os.environ['OUT'] +"/"+ instrumented_binary, '--use-gcc', assembler
    ],
                   check=True)


def build():
    """
    Build benchmark and copy fuzzer to $OUT.
    """
    build_uninstrumented_benchmark()
    instrument_binary()
    shutil.copy('/src/afl/afl-fuzz', os.environ['OUT'])


def prepare_fuzz_environment(input_corpus):
    """
    Prepare to fuzz with AFL or another AFL-based fuzzer.
    """
    # Tell AFL to not use its terminal UI so we get usable logs.
    os.environ['AFL_NO_UI'] = '1'
    # Skip AFL's CPU frequency check (fails on Docker).
    os.environ['AFL_SKIP_CPUFREQ'] = '1'
    # No need to bind affinity to one core, Docker enforces 1 core usage.
    os.environ['AFL_NO_AFFINITY'] = '1'
    # AFL will abort on startup if the core pattern sends notifications to
    # external programs. We don't care about this.
    os.environ['AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES'] = '1'
    # Don't exit when crashes are found. This can happen when corpus from
    # OSS-Fuzz is used.
    os.environ['AFL_SKIP_CRASHES'] = '1'
    # Shuffle the queue
    os.environ['AFL_SHUFFLE_QUEUE'] = '1'
    # AFL needs at least one non-empty seed to start.
    utils.create_seed_file_for_empty_corpus(input_corpus)


def fuzz(input_corpus, output_corpus, target_binary):
    """
    Run fuzzer.

    Arguments:
      input_corpus: Directory containing the initial seed corpus for
                    the benchmark.
      output_corpus: Output directory to place the newly generated corpus
                     from fuzzer run.
      target_binary: Absolute path to the fuzz target binary.
    """

    prepare_fuzz_environment(input_corpus)
    instrumented_binary = target_binary + '.dafl'

    subprocess.call([
        './afl-fuzz', '-i', input_corpus, '-o', output_corpus, '--',
        instrumented_binary, '@@'
    ])