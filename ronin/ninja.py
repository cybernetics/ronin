# Copyright 2016-2017 Tal Liron
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from .contexts import current_context
from .projects import Project
from .phases import Phase
from .executors import Executor
from .utils.paths import join_path, change_extension
from .utils.strings import stringify
from .utils.platform import which
from .utils.collections import dedup
from .utils.types import verify_type
from .utils.messages import announce
from cStringIO import StringIO
from os import makedirs
from subprocess import check_call, CalledProcessError
from datetime import datetime
from textwrap import wrap
import sys, os

# See:
# https://ninja-build.org/manual.html#_ninja_file_reference
# https://github.com/ninja-build/ninja/blob/master/misc/ninja_syntax.py

DEFAULT_NAME = 'build.ninja'
DEFAULT_COLUMNS = 100

def configure_ninja(command=None, file_name=None, columns=None, strict=None):
    with current_context(False) as ctx:
        ctx.ninja.command = command
        ctx.ninja.file_name = file_name
        ctx.ninja.file_columns = columns
        ctx.ninja.file_strict = strict

class NinjaFile(object):
    """
    Manages a `Ninja build system <https://ninja-build.org/>`__ file.
    """
    
    def __init__(self, project, command=None, file_name=None, columns=None, strict=None):
        verify_type(project, Project)
        self._project = project
        self.command = None
        self.file_name = file_name or ('%s.ninja' % project.file_name if project.file_name is not None else None)
        self.columns = None
        self.strict = None
    
    def __str__(self):
        io = StringIO()
        try:
            self.write(io)
            v = io.getvalue()
        finally:
            io.close()
        return v
    
    @property
    def base_path(self):
        with current_context() as ctx:
            return join_path(ctx.paths.output, self._project.variant)

    @property
    def path(self):
        with current_context() as ctx:
            file_name = stringify(ctx.fallback(self.file_name, 'ninja.file_name', DEFAULT_NAME))
        return join_path(self.base_path, file_name)

    def generate(self):
        base_path = self.base_path
        path = self.path
        announce("Generating '%s'" % path)
        if not os.path.isdir(base_path):
            makedirs(base_path)
        with open(path, 'w') as io:
            self.write(io)

    def remove(self):
        path = self.path
        if os.path.isfile(path):
            os.remove(path)

    def build(self):
        self.generate()
        path = self.path
        with current_context() as ctx:
            command = which(ctx.fallback(self.command, 'ninja.command', 'ninja'), True)
            verbose = ctx.get('cli.verbose', False)
        args = [command, '-f', path]
        if verbose:
            args.append('-v')
        try:
            check_call(args)
        except CalledProcessError as e:
            return e.returncode
        return 0

    def clean(self):
        with current_context() as ctx:
            results = ctx.get('build._phase_results')
            if results is not None:
                results[self._project] = None
        path = self.path
        if os.path.isfile(path):
            with current_context() as ctx:
                command = which(ctx.fallback(self.command, 'ninja.command', 'ninja'), True)
            args = [command, '-f', path, '-t', 'clean', '-g']
            try:
                check_call(args)
            except CalledProcessError as e:
                return e.returncode
        self.remove()
        return 0

    def delegate(self):
        sys.exit(self.build())

    def write(self, io):
        with current_context() as ctx:
            columns = ctx.fallback(self.columns, 'ninja.file_columns', DEFAULT_COLUMNS)
            strict = ctx.fallback(self.strict, 'ninja.file_columns_strict', False)
            if strict and (columns is not None) and (columns < _MINIMUM_COLUMNS_STRICT):
                columns = _MINIMUM_COLUMNS_STRICT
        with _Writer(io, columns, strict) as w:
            w.comment('Ninja file for %s' % self._project)
            w.comment('Generated by Ronin on %s' % datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'))
            if columns is not None:
                w.comment('Columns: %d (%s)' % (columns, 'strict' if strict else 'non-strict'))
            all_phase_outputs = {}
            for phase_name, phase in self._project.phases.iteritems():
                verify_type(phase, Phase)
                self._write_rule(w, phase_name, phase, all_phase_outputs)
                
    def _write_rule(self, w, phase_name, phase, all_phase_outputs):
        # Check if already written
        if phase_name in all_phase_outputs:
            return
        
        # Input from other phases
        inputs_from_names = self._get_inputs_from_names(phase, all_phase_outputs, w)
        
        phase_outputs = []
        all_phase_outputs[phase_name] = phase_outputs
        with current_context() as ctx:
            results = ctx.get('build._phase_results')
        if results is not None:
            phase_results = results.get(phase)
            if phase_results is None:
                phase_results = []
                results[phase] = phase_results
        else:
            phase_results = None
        
        rule_name = phase_name.replace(' ', '_')

        # Rule
        w.line()
        w.line('rule %s' % rule_name)
        
        # Description
        description = stringify(phase.description)
        if description is None:
            description = '%s $out' % phase_name
        w.line('description = %s' % description, 1)

        # Command
        verify_type(phase.executor, Executor)
        command = phase.command_as_str(_escape)
        w.line('command = %s' % command, 1)
        
        # Deps
        deps_file = stringify(phase.executor._deps_file)
        if deps_file:
            w.line('depfile = %s' % deps_file, 1)
            deps_type = stringify(phase.executor._deps_type)
            if deps_type:
                w.line('deps = %s' % deps_type, 1)

        # Paths
        with current_context() as ctx:
            input_base = ctx.get('paths.input')
            if not input_base.endswith(os.sep):
                input_base += os.sep
            
            output_type = phase.executor.output_type
            if output_type == 'object':
                output_base = ctx.get('paths.object')
                if output_base is None:
                    output_base = join_path(self.base_path, ctx.get('paths.object_relative'))
            elif output_type == 'binary':
                output_base = ctx.get('paths.binary')
                if output_base is None:
                    output_base = join_path(self.base_path, ctx.get('paths.binary_relative'))
        
        # Single output?
        if phase.output:
            output = join_path(output_base, phase.output)
        else:
            output = None

        # Inputs
        inputs = phase.inputs
        for inputs_from_name in inputs_from_names:
            inputs += all_phase_outputs[inputs_from_name]
        inputs = dedup(inputs)

        # Extension
        output_prefix = stringify(phase.executor.output_prefix) or ''
        output_extension = stringify(phase.executor.output_extension)

        if output:
            # Single output
            w.line()
            
            output = output_prefix + change_extension(output, output_extension)
            if inputs:
                w.line('build %s: %s %s' % (_pathify(output), rule_name, ' '.join([_pathify(v) for v in inputs])))
            else:
                w.line('build %s: %s' % (_pathify(output), rule_name))
            phase_outputs.append(output)
            if phase_results is not None:
                phase_results.append(output)
        elif inputs:
            # Multiple outputs
            w.line()
            
            input_base_length = len(input_base)
            for input in inputs:
                output = input
                if output.startswith(input_base):
                    output = output[input_base_length:]
                output = change_extension(output, output_extension)
                output = join_path(output_base, output)
                w.line('build %s: %s %s' % (_pathify(output), rule_name, _pathify(input)))
                phase_outputs.append(output)
                if phase_results is not None:
                    phase_results.append(output)

    def _get_inputs_from_names(self, phase, all_phase_outputs, w):
        names = []
        for value in phase.inputs_from:
            if isinstance(value, Phase):
                name = self._project.get_phase_name(value)
                inputs_from = value
                if name is None:
                    raise AttributeError('inputs_from contains a phase that is not in the project')
            else:
                name = stringify(value)
                inputs_from = self._project.phases.get(name)
                if inputs_from is None:
                    raise AttributeError('inputs_from "%s" is not a phase in the project' % name)
            if inputs_from is phase:
                raise AttributeError('inputs_from contains self')

            names.append(name)
            self._write_rule(w, name, inputs_from, all_phase_outputs)
            
        return names

_MINIMUM_COLUMNS_STRICT = 30 # lesser than this can lead to breakage
_INDENT = '  '

def _escape(value):
    value = stringify(value)
    return value.replace('$', '$$')

def _pathify(value):
    value = stringify(value)
    return value.replace('$ ', '$$ ').replace(' ', '$ ').replace(':', '$:')

class _Writer(object):
    def __init__(self, io, columns, strict):
        self._io = io
        self._columns = columns
        self._strict = strict

    def __enter__(self):
        return self
    
    def __exit__(self, the_type, value, traceback):
        pass
    
    def line(self, line='', indent=0):
        indentation = _INDENT * indent
        if self._columns is None:
            self._io.write('%s%s\n' % (indentation, line))
        else:
            leading_space_length = len(indentation)
            broken = False
                
            while leading_space_length + len(line) > self._columns:
                width = self._columns - leading_space_length - 2
                
                # First try: find last un-escaped space within width 
                space = width
                while True:
                    space = line.rfind(' ', 0, space)
                    if (space < 0) or _Writer._is_unescaped(line, space):
                        break                

                # Second try (if non-strict): find first un-escaped space after width
                if (space < 0) and (not self._strict):
                    space = width - 1
                    while True:
                        space = line.find(' ', space + 1)
                        if (space < 0) or _Writer._is_unescaped(line, space):
                            break

                if space != -1:
                    # Break at space
                    self._io.write('%s%s $\n' % (indentation, line[:space]))
                    line = line[space + 1:]
                    if not broken:
                        # Indent                               
                        broken = True
                        indentation += _INDENT
                        leading_space_length += len(_INDENT)
                elif self._strict:
                    # Break anywhere
                    width += 1
                    self._io.write('%s%s$\n' % (indentation, line[:width]))
                    line = line[width:]
                else:
                    break

            self._io.write('%s%s\n' % (indentation, line))

    def comment(self, line):
        if self._columns is None:
            self._io.write('# %s\n' % line)
        else:
            width = self._columns - 2
            lines = wrap(line, width, break_long_words=self._strict, break_on_hyphens=False)
            for line in lines:
                self._io.write('# %s\n' % line)

    @staticmethod        
    def _is_unescaped(line, i):
        dollar_count = 0
        dollar_index = i - 1
        while (dollar_index > 0) and (line[dollar_index] == '$'):
            dollar_count += 1
            dollar_index -= 1
        return dollar_count % 2 == 0
