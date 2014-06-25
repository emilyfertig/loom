# Copyright (c) 2014, Salesforce.com, Inc.  All rights reserved.
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

import os
from distributions.fileutil import tempdir
import loom.format
import loom.generate
import loom.config
import loom.runner
from loom.test.util import for_each_dataset, CLEANUP_ON_ERROR, assert_found


def make_config(config_out):
    config = {'schedule': {'extra_passes': 2}}
    loom.config.fill_in_defaults(config)
    loom.config.config_dump(config, config_out)


@for_each_dataset
def test_ingest_infer(schema, rows_csv, **unused):
    with tempdir(cleanup_on_error=CLEANUP_ON_ERROR):
        encoding = os.path.abspath('encoding.json.gz')
        rows = os.path.abspath('rows.pbs.gz')
        init = os.path.abspath('init.pb.gz')
        config = os.path.abspath('config.pb.gz')
        model = os.path.abspath('model.pb.gz')
        groups = os.path.abspath('groups')
        assign = os.path.abspath('assign.pbs.gz')
        log = os.path.abspath('log.pbs.gz')
        os.mkdir(groups)

        print 'ingesting'
        loom.format.ingest(
            schema_in=schema,
            rows_in=rows_csv,
            encoding_out=encoding,
            rows_out=rows,
            debug=True)
        assert_found(encoding, rows)

        print 'generating init'
        loom.generate.generate_init(
            encoding_in=encoding,
            model_out=init)
        assert_found(init)

        print 'creating config'
        make_config(config_out=config)
        assert_found(config)

        print 'inferring'
        loom.runner.infer(
            config_in=config,
            rows_in=rows,
            model_in=init,
            model_out=model,
            groups_out=groups,
            assign_out=assign,
            log_out=log,
            debug=True)
        assert_found(model, groups, assign, log)