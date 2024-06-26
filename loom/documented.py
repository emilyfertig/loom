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

import os
import fnmatch
import loom
import loom.store
import parsable
parsable = parsable.Parsable()

DOC = os.path.join(loom.ROOT, 'doc')
TRANSFORMS = {}
COLORS = {
    None: 'pink',
    'test': 'cyan',
}
WEIGHTS = {
    None: 1.0,
    'test': 0.1,
}


def transform(**kwargs):

    def decorator(fun):
        key = (fun.__module__, fun.__name__)
        loom.documented.TRANSFORMS[key] = dict(kwargs)
        return fun

    return decorator


def import_all_loom_modules():
    for root, dirnames, filenames in os.walk(os.path.join(loom.ROOT, 'loom')):
        for filename in fnmatch.filter(filenames, '*.py'):
            path = os.path.relpath(os.path.join(root, filename), loom.ROOT)
            module_name = path.replace('/', '.')[:-len('.py')]
            __import__(module_name)


def write_graphviz(datas, transforms, filename):
    with open(filename, 'w') as f:
        o = lambda line: f.write(line + '\n')

        o('// this file was generated by {}'.format(
            os.path.relpath(__file__, loom.ROOT)))
        o('digraph G {')
        o('  overlap=false;')
        o('  graph [fontname = "helvetica"];')
        o('  node [fontname = "helvetica"];')
        o('  edge [fontname = "helvetica"];')
        o('')
        o('  // data')
        o('  {')
        o('    node [')
        o('      shape=Mrecord,')
        o('      style="filled",')
        o('      color="#dddddd",')
        o('      fillcolor="#eeeeee"')
        o('    ];')
        for name, label in datas:
            o('    {} [label={}];'.format(name, label))
        o('  }')
        o('')
        o('  // transforms')
        o('  {')
        o('    node [shape=box, style="filled,setlinewidth(0)"];')
        o('')
        for (module, name), props in transforms:
            color = COLORS[props.get('role')]
            label = '<{}<BR/>{}>'.format(
                '<FONT POINT-SIZE="16">{}.</FONT>'.format(module),
                '<FONT POINT-SIZE="24">{}</FONT>'.format(name))
            o('    {} [label={}, fillcolor={}];'.format(name, label, color))
        o('')
        for (module, name), props in transforms:
            weight = WEIGHTS[props.get('role')]
            for data in props.get('inputs', []):
                data = data.replace('.', '_')
                o('    {} -> {} [weight={}];'.format(data, name, weight))
            for data in props.get('outputs', []):
                data = data.replace('.', '_')
                o('    {} -> {} [weight={}];'.format(name, data, weight))
        o('  }')
        o('}')


@parsable.command
def make_dataflow(test=False, filenames=True):
    '''
    Make $LOOM/doc/dataflow.dot
    '''
    import_all_loom_modules()
    transforms = loom.documented.TRANSFORMS
    assert transforms, transforms

    if not test:
        transforms = {
            key: props
            for key, props in transforms.items()
            if props.get('role') != 'test'
        }

    datas = {}
    paths = loom.store.get_paths('dataset')
    root = paths['root']
    for props in transforms.values():
        for key in props.get('inputs', []) + props.get('outputs', []):
            name = key.replace('.', '_')
            if filenames and '.' in key:
                path = os.path.relpath(loom.store.get_path(paths, key), root)
                datas[name] = '<{}<BR/>{}>'.format(
                    '<FONT POINT-SIZE="18">{}</FONT>'.format(key),
                    '<FONT POINT-SIZE="12">{}</FONT>'.format(path))
            else:
                datas[name] = '<<FONT POINT-SIZE="18">{}</FONT>>'.format(key)

    datas = sorted(list(datas.items()))
    transforms = sorted(list(transforms.items()), key=lambda item: item[0])

    filename = os.path.join(DOC, 'dataflow.dot')
    write_graphviz(datas, transforms, filename)


if __name__ == '__main__':
    parsable.dispatch()
