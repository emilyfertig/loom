# Copyright (c) 2014, Salesforce.com, Inc.  All rights reserved.
# Copyright (c) 2015, Google, Inc.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# - Redistributions of source code must retain the above copyright
#   notice, this list of conditions and the following disclaimer.
# - Redistributions in binary form must reproduce the above copyright
#   notice, this list of conditions and the following disclaimer in the
#   documentation and/or other materials provided with the distribution.
# - Neither the name of Salesforce.com nor the names of its contributors
#   may be used to endorse or promote products derived from this
#   software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
# OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR
# TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
# USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import itertools
import os
import pickle
import sys
import csv
import shutil
import tempfile
import traceback
import contextlib
import multiprocessing
import simplejson as json
from google.protobuf.descriptor import FieldDescriptor
from distributions.io.stream import open_compressed
from distributions.io.stream import json_load
from distributions.io.stream import protobuf_stream_load
import loom.schema_pb2
import parsable
parsable = parsable.Parsable()

THREADS = int(os.environ.get('LOOM_THREADS', multiprocessing.cpu_count()))
VERBOSITY = int(os.environ.get('LOOM_VERBOSITY', 1))


class LoomError(Exception):
    pass


class KnownBug(LoomError):
    pass


def fixme(name, message):
    message = 'FIXME({}) {}'.format(name, message)
    if 'nose' in sys.modules:
        import nose
        return nose.SkipTest(message)
    else:
        return KnownBug(message)


def LOG(message):
    if VERBOSITY:
        sys.stdout.write('{}\n'.format(message))
        sys.stdout.flush()


@contextlib.contextmanager
def chdir(wd):
    oldwd = os.getcwd()
    try:
        os.chdir(wd)
        yield wd
    finally:
        os.chdir(oldwd)


@contextlib.contextmanager
def tempdir(cleanup_on_error=True):
    oldwd = os.getcwd()
    wd = tempfile.mkdtemp()
    try:
        os.chdir(wd)
        yield wd
        cleanup_on_error = True
    finally:
        os.chdir(oldwd)
        if cleanup_on_error:
            shutil.rmtree(wd)


@contextlib.contextmanager
def temp_copy(infile):
    infile = os.path.abspath(infile)
    dirname, basename = os.path.split(infile)
    outfile = os.path.join(dirname, 'temp.{}'.format(basename))
    try:
        yield outfile
        os.rename(outfile, infile)
    finally:
        rm_rf(outfile)


def mkdir_p(dirname):
    'like mkdir -p'
    if not os.path.exists(dirname):
        try:
            os.makedirs(dirname)
        except OSError as e:
            if not os.path.exists(dirname):
                raise e


def rm_rf(path):
    'like rm -rf'
    if os.path.exists(path):
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)


def cp_ns(source, destin):
    'like cp -ns, link destin to source if destin does not exist'
    if not os.path.exists(destin):
        assert os.path.exists(source), source
        dirname = os.path.dirname(destin)
        if dirname:
            mkdir_p(dirname)
        try:
            os.symlink(source, destin)
        except OSError as e:
            if not os.path.exists(destin):
                raise e


def print_trace(fun, args):
    try:
        return fun(*args)
    except Exception as e:
        print(e)
        traceback.print_exc()
        raise


def parallel_map(fun, args):
    if not isinstance(args, list):
        args = list(args)
    fun_args = [(fun, arg) for arg in args]
    is_daemon = multiprocessing.current_process().daemon
    if THREADS == 1 or len(args) < 2 or is_daemon:
        LOG('Running {} in this thread'.format(fun.__name__))
        return list(itertools.starmap(print_trace, fun_args))
    else:
        LOG('Running {} in {:d} threads'.format(fun.__name__, THREADS))
        # FIXME: Multiprocessing hangs.
        # pool = multiprocessing.Pool(THREADS)
        # return pool.starmap(print_trace, fun_args, chunksize=1)
        return list(itertools.starmap(print_trace, fun_args))


@contextlib.contextmanager
def csv_reader(filename, mode='rt'):
    with open_compressed(filename, mode) as f:
        yield csv.reader(f)


@contextlib.contextmanager
def csv_writer(filename):
    with open_compressed(filename, 'wt') as f:
        yield csv.writer(f)


def pickle_dump(data, filename):
    with open_compressed(filename, 'wb') as f:
        pickle.dump(data, f)


def pickle_load(filename):
    with open_compressed(filename, 'rb') as f:
        return pickle.load(f)


def protobuf_to_dict(message):
    assert message.IsInitialized()
    raw = {}
    for field in message.DESCRIPTOR.fields:
        value = getattr(message, field.name)
        if field.label == FieldDescriptor.LABEL_REPEATED:
            if field.type == FieldDescriptor.TYPE_MESSAGE:
                value = map(protobuf_to_dict, value)
            else:
                value = list(value)
            if len(value) == 0:
                value = None
        else:
            if field.type == FieldDescriptor.TYPE_MESSAGE:
                if value.IsInitialized():
                    value = protobuf_to_dict(value)
                else:
                    value = None
        if value is not None:
            raw[field.name] = value
    return raw


def dict_to_protobuf(raw, message):
    assert isinstance(raw, dict)
    for key, raw_value in raw.items():
        if isinstance(raw_value, dict):
            value = getattr(message, key)
            dict_to_protobuf(raw_value, value)
        elif isinstance(raw_value, list):
            value = getattr(message, key)
            list_to_protobuf(raw_value, value)
        else:
            setattr(message, key, raw_value)


def list_to_protobuf(raw, message):
    assert isinstance(raw, list)
    if raw:
        if isinstance(raw[0], dict):
            for value in raw:
                dict_to_protobuf(value, message.add())
        elif isinstance(raw[0], list):
            for value in raw:
                list_to_protobuf(value, message.add())
        else:
            message[:] = raw


GUESS_MESSAGE_TYPE = {
    'rows': 'Row',
    'diffs': 'Row',
    'shuffled': 'Row',
    'tares': 'ProductValue',
    'schema': 'ProductValue',
    'assign': 'Assignment',
    'model': 'CrossCat',
    'init': 'CrossCat',
    'mixture': 'ProductModel.Group',
    'config': 'Config',
    'checkpoint': 'Checkpoint',
    'log': 'LogMessage',
    'infer_log': 'LogMessage',
    'query_log': 'LogMessage',
    'requests': 'Query.Request',
    'responses': 'Query.Response',
}


def get_message(filename, message_type='guess'):
    if message_type == 'guess':
        prefix = os.path.basename(filename).split('.')[0]
        try:
            message_type = GUESS_MESSAGE_TYPE[prefix]
        except KeyError:
            raise LoomError(
                'Cannot guess message type for {}'.format(filename))
    Message = loom.schema_pb2
    for attr in message_type.split('.'):
        Message = getattr(Message, attr)
    return Message()


@parsable.command
def pretty_print(filename, message_type='guess'):
    '''
    Print text/json/protobuf messages from a raw/gz/bz2 file.
    '''
    parts = os.path.basename(filename).split('.')
    if parts[-1] in ['gz', 'bz2']:
        parts.pop()
    protocol = parts[-1]
    if protocol == 'json':
        data = json_load(filename)
        print(json.dumps(data, sort_keys=True, indent=4))
    elif protocol == 'pb':
        message = get_message(filename, message_type)
        with open_compressed(filename, 'rb') as f:
            message.ParseFromString(f.read())
            print(message)
    elif protocol == 'pbs':
        message = get_message(filename, message_type)
        for string in protobuf_stream_load(filename):
            message.ParseFromString(string)
            print(message)
    elif protocol == 'pickle':
        data = pickle_load(filename)
        print(repr(data))
    else:
        with open_compressed(filename) as f:
            for line in f:
                print(line)


@parsable.command
def cat(*filenames):
    '''
    Print text/json/protobuf messages from multiple raw/gz/bz2 files.
    '''
    for filename in filenames:
        pretty_print(filename)


if __name__ == '__main__':
    parsable.dispatch()
