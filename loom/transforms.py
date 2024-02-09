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

import math
import os
import re
import datetime
import dateutil.parser
from collections import Counter
from contextlib2 import ExitStack
from distributions.io.stream import json_dump
from distributions.io.stream import json_load
import loom.util
from loom.util import cp_ns
from loom.util import LOG
from loom.util import parallel_map
from loom.util import pickle_dump
from loom.util import pickle_load
from loom.format import load_encoder
from loom.format import load_decoder
import loom.documented
import parsable
parsable = parsable.Parsable()

EXAMPLE_VALUES = {
    'boolean': ['0', '1', 'true', 'false'],
    'categorical': ['Monday', 'June'],
    'unbounded_categorical': ['CRM', '90210'],
    'count': ['0', '1', '2', '3', '4'],
    'real': ['-100.0', '1e-4'],
    'sparse_real': ['0', '0', '0', '0', '123456.78', '0', '0', '0'],
    'date': ['2014-03-31', '10pm, August 1, 1979'],
    'text': ['This is a text feature.', 'Hello World!'],
    'tags': ['', 'big_data machine_learning platform'],
}
for fluent_type, values in EXAMPLE_VALUES.copy().items():
    EXAMPLE_VALUES['optional_{}'.format(fluent_type)] = [''] + values
EXAMPLE_VALUES['id'] = ['any unique string can serve as an id']

FLUENT_TO_BASIC = {
    'boolean': 'bb',
    'categorical': 'dd',
    'unbounded_categorical': 'dpd',
    'count': 'gp',
    'real': 'nich',
}

encode_bool = load_encoder({'model': 'bb'})
decode_bool = load_decoder({'model': 'bb'})


def get_row_dict(header, row):
    '''By convention, empty strings are omitted from the result dict.'''
    return {key: value for key, value in zip(header, row) if value}


class TransformSequence(object):
    def __init__(self, transforms):
        self.transforms = transforms

    def forward_set(self, feature_set):
        result = set(feature_set)
        for t in self.transforms:
            t.forward_set(result)
        return result

    def forward_dict(self, header_out, row_dict):
        for t in self.transforms:
            t.forward(row_dict)
        return [row_dict.get(key) for key in header_out]

    def forward_row(self, header_in, header_out, row):
        row_dict = get_row_dict(header_in, row)
        for t in self.transforms:
            t.forward(row_dict)
        return [row_dict.get(key) for key in header_out]

    def backward_row(self, header_in, header_out, row):
        row_dict = get_row_dict(header_in, row)
        for t in reversed(self.transforms):
            t.backward(row_dict)
        return [row_dict.get(key) for key in header_out]


def load_transforms(filename):
    transforms = pickle_load(filename) if os.path.exists(filename) else []
    return TransformSequence(transforms)


# ----------------------------------------------------------------------------
# simple transforms


class StringTransform(object):
    def __init__(self, feature_name, fluent_type):
        self.feature_name = feature_name
        self.basic_type = FLUENT_TO_BASIC[fluent_type]

    def get_schema(self):
        return {self.feature_name: self.basic_type}

    def forward_set(self, feature_set):
        pass

    def forward(self, row_dict):
        feature_name = self.feature_name
        if feature_name in row_dict:
            row_dict[feature_name] = row_dict[feature_name].lower()

    def backward(self, row_dict):
        pass


class PercentTransform(object):
    def __init__(self, feature_name):
        self.feature_name = feature_name

    def get_schema(self):
        return {self.feature_name: 'nich'}

    def forward_set(self, feature_set):
        pass

    def forward(self, row_dict):
        feature_name = self.feature_name
        if feature_name in row_dict:
            value = float(row_dict[feature_name].replace('%', '')) * 1e-2
            row_dict[feature_name] = str(value)

    def backward(self, row_dict):
        feature_name = self.feature_name
        if feature_name in row_dict:
            value = '{}%'.format(float(row_dict[feature_name]) * 1e2)
            row_dict[feature_name] = value


class PresenceTransform(object):
    def __init__(self, feature_name):
        self.feature_name = feature_name
        self.present_name = '{}.present'.format(feature_name)
        self.value_name = '{}.value'.format(feature_name)

    def get_schema(self):
        return {self.present_name: 'bb'}

    def forward_set(self, feature_set):
        if self.feature_name in feature_set:
            feature_set.add(self.present_name)
            feature_set.add(self.value_name)

    def forward(self, row_dict):
        present = self.feature_name in row_dict
        row_dict[self.present_name] = decode_bool(present)
        if present:
            row_dict[self.value_name] = row_dict[self.feature_name]

    def backward(self, row_dict):
        if self.present_name in row_dict:
            if encode_bool(row_dict[self.present_name]):
                row_dict[self.feature_name] = row_dict[self.value_name]
            else:
                if self.feature_name in row_dict:
                    del row_dict[self.feature_name]  # nonmonotone


class SparseRealTransform(object):
    def __init__(self, feature_name, tare_value=0.0):
        self.feature_name = feature_name
        self.nonzero_name = '{}.nonzero'.format(feature_name)
        self.value_name = '{}.value'.format(feature_name)
        self.tare_value = str(float(tare_value))

    def get_schema(self):
        return {self.nonzero_name: 'bb', self.value_name: 'nich'}

    def forward_set(self, feature_set):
        if self.feature_name in feature_set:
            feature_set.add(self.nonzero_name)
            feature_set.add(self.value_name)

    def forward(self, row_dict):
        feature_name = self.feature_name
        if feature_name in row_dict:
            value = float(row_dict[feature_name])
            nonzero = (value != self.tare_value)
            row_dict[self.nonzero_name] = decode_bool(nonzero)
            if nonzero:
                row_dict[self.value_name] = value

    def backward(self, row_dict):
        if self.nonzero_name in row_dict:
            if encode_bool(row_dict[self.nonzero_name]):
                if self.value_name in row_dict:
                    row_dict[self.feature_name] = row_dict[self.value_name]
            else:
                row_dict[self.feature_name] = self.tare_value


# ----------------------------------------------------------------------------
# text transform

MIN_WORD_FREQ = 0.01

split_text = re.compile('\W+').split


def get_word_set(text):
    return frozenset(s for s in split_text(text.lower()) if s)


class TextTransformBuilder(object):
    def __init__(
            self,
            feature_name,
            allow_empty=False,
            min_word_freq=MIN_WORD_FREQ):
        self.feature_name = feature_name
        self.counts = Counter()
        self.min_word_freq = min_word_freq
        self.allow_empty = allow_empty

    def add_row(self, row_dict):
        text = row_dict.get(self.feature_name, '')
        self.counts.update(get_word_set(text))

    def build(self):
        counts = self.counts.most_common()
        max_count = counts[0][1]
        min_count = self.min_word_freq * max_count
        words = [word for word, count in counts if count > min_count]
        return TextTransform(self.feature_name, words, self.allow_empty)


class TextTransform(object):
    def __init__(self, feature_name, words, allow_empty):
        self.feature_name = feature_name
        self.features = [
            ('{}.{}'.format(feature_name, word), word)
            for word in words
        ]
        self.allow_empty = allow_empty

    def get_schema(self):
        return {feature_name: 'bb' for feature_name, _ in self.features}

    def forward_set(self, feature_set):
        if self.feature_name in feature_set:
            for feature_name, word in self.features:
                feature_set.add(feature_name)

    def forward(self, row_dict):
        if self.feature_name in row_dict or self.allow_empty:
            text = row_dict.get(self.feature_name, '')
            word_set = get_word_set(text)
            for feature_name, word in self.features:
                row_dict[feature_name] = '1' if word in word_set else '0'

    def backward(self, row_dict):
        row_dict[self.feature_name] = ' '.join([
            word
            for feature_name, word in self.features
            if row_dict.get(feature_name, False)
        ])


# ----------------------------------------------------------------------------
# date transform

EPOCH = dateutil.parser.parse('2014-03-31')  # arbitrary (Loom's birthday)


def days_between(start, end):
    return (end - start).total_seconds() / (24 * 60 * 60)


class DateTransform(object):
    def __init__(self, feature_name, relatives):
        self.feature_name = feature_name
        self.relatives = relatives
        suffices = ['absolute', 'mod.year', 'mod.month', 'mod.week', 'mod.day']
        self.abs_names = {
            suffix: '{}.{}'.format(feature_name, suffix)
            for suffix in suffices
        }
        self.rel_names = {
            relative: '{}.minus.{}'.format(feature_name, relative)
            for relative in relatives
        }

    def get_schema(self):
        schema = {
            self.abs_names['absolute']: 'nich',
            self.abs_names['mod.year']: 'dpd',
            self.abs_names['mod.month']: 'dpd',
            self.abs_names['mod.week']: 'dpd',
            self.abs_names['mod.day']: 'dpd',
        }
        for rel_name in self.rel_names.values():
            schema[rel_name] = 'nich'
        return schema

    def forward_set(self, feature_set):
        if self.feature_name in feature_set:
            for feature_name in self.abs_names.values():
                feature_set.add(feature_name)
            for relative, feature_name in self.rel_names.items():
                if relative in feature_set:
                    feature_set.add(feature_name)

    def forward(self, row_dict):
        if self.feature_name in row_dict:
            date = dateutil.parser.parse(row_dict[self.feature_name])

            abs_names = self.abs_names
            row_dict[abs_names['absolute']] = days_between(EPOCH, date)
            row_dict[abs_names['mod.year']] = date.month
            row_dict[abs_names['mod.month']] = date.day
            row_dict[abs_names['mod.week']] = date.weekday()
            row_dict[abs_names['mod.day']] = date.hour

            for relative, rel_name in self.rel_names.items():
                if relative in row_dict:
                    other_date = dateutil.parser.parse(row_dict[relative])
                    row_dict[rel_name] = days_between(other_date, date)

    def backward(self, row_dict):
        # only attempt backward transform if feature.absolute is present
        abs_name = self.abs_names['absolute']
        if abs_name in row_dict:
            days_since_epoch = float(row_dict[abs_name])
            if not math.isnan(days_since_epoch):
                date = EPOCH + datetime.timedelta(days_since_epoch)
                row_dict[self.feature_name] = str(date)


# ----------------------------------------------------------------------------
# building transforms

def load_schema(schema_csv):
    fluent_schema = {}
    with loom.util.csv_reader(schema_csv) as reader:
        next(reader)  # ignore header; parse positionally
        for row in reader:
            if len(row) >= 2:
                feature_name = row[0]
                fluent_type = row[1]
                if fluent_type:
                    fluent_schema[feature_name] = fluent_type
    return fluent_schema


def build_transforms(rows_in, transforms, builders):
    if os.path.isdir(rows_in):
        filenames = [os.path.join(rows_in, f) for f in os.listdir(rows_in)]
    else:
        filenames = [rows_in]
    for filename in filenames:
        with loom.util.csv_reader(filename) as reader:
            header = next(reader)
            for row in reader:
                row_dict = get_row_dict(header, row)
                for transform in transforms:
                    transform.forward(row_dict)
                for builder in builders:
                    builder.add_row(row_dict)
    return [builder.build() for builder in builders]


@loom.documented.transform(
    inputs=['schema_csv', 'rows_csv'],
    outputs=['ingest.schema', 'ingest.transforms'])
@parsable.command
def make_transforms(schema_in, rows_in, schema_out, transforms_out):
    fluent_schema = load_schema(schema_in)
    basic_schema = {}
    pre_transforms = []
    transforms = []
    builders = []
    dates = [
        feature_name
        for feature_name, fluent_type in fluent_schema.items()
        if fluent_type.endswith('date')
    ]
    id_field = None
    for feature_name, fluent_type in fluent_schema.items():
        # parse adjectives
        if fluent_type.startswith('optional_'):
            transform = PresenceTransform(feature_name)
            pre_transforms.append(transform)
            transforms.append(transform)
            fluent_type = fluent_type[len('optional_'):]
            feature_name = '{}.value'.format(feature_name)

        # parse nouns
        if fluent_type == 'id':
            id_field = feature_name
        elif fluent_type in ['categorical', 'unbounded_categorical']:
            transforms.append(StringTransform(feature_name, fluent_type))
        elif fluent_type == 'percent':
            transforms.append(PercentTransform(feature_name))
        elif fluent_type == 'sparse_real':
            transforms.append(SparseRealTransform(feature_name))
        elif fluent_type == 'text':
            builders.append(TextTransformBuilder(feature_name))
        elif fluent_type == 'tags':
            builders.append(
                TextTransformBuilder(feature_name, allow_empty=True))
        elif fluent_type == 'date':
            relatives = [other for other in dates if other < feature_name]
            transforms.append(DateTransform(feature_name, relatives))
        else:
            basic_type = FLUENT_TO_BASIC[fluent_type]
            basic_schema[feature_name] = basic_type
    if builders:
        transforms += build_transforms(rows_in, pre_transforms, builders)
    for transform in transforms:
        basic_schema.update(transform.get_schema())
    json_dump(basic_schema, schema_out)
    pickle_dump(transforms, transforms_out)
    LOG('transformed {} -> {} features'.format(
        len(fluent_schema),
        len(basic_schema)))
    return id_field


# ----------------------------------------------------------------------------
# applying transforms


def _transform_rows(transform, transformed_header, rows_in, rows_out):
    with ExitStack() as stack:
        with_ = stack.enter_context
        reader = with_(loom.util.csv_reader(rows_in))
        writer = with_(loom.util.csv_writer(rows_out))
        header = next(reader)
        writer.writerow(transformed_header)
        for row in reader:
            row = transform.forward_row(header, transformed_header, row)
            writer.writerow(row)


@loom.documented.transform(
    inputs=['ingest.schema', 'ingest.transforms', 'rows_csv'],
    outputs=['ingest.rows_csv'])
@parsable.command
def transform_rows(schema_in, transforms_in, rows_in, rows_out, id_field=None):
    transforms = pickle_load(transforms_in)
    if not transforms:
        cp_ns(rows_in, rows_out)
    else:
        transform = TransformSequence(transforms)
        transformed_header = sorted(json_load(schema_in).keys())
        if id_field is not None:
            assert id_field not in transformed_header
            transformed_header = [id_field] + transformed_header
        tasks = []
        if os.path.isdir(rows_in):
            loom.util.mkdir_p(rows_out)
            for f in os.listdir(rows_in):
                tasks.append((
                    transform,
                    transformed_header,
                    os.path.join(rows_in, f),
                    os.path.join(rows_out, f),
                ))
        else:
            tasks.append((transform, transformed_header, rows_in, rows_out))
        parallel_map(_transform_rows, tasks)


def make_fake_transforms(transforms_out):
    pickle_dump([], transforms_out)


if __name__ == '__main__':
    parsable.dispatch()
