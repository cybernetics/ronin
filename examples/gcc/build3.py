#!/usr/bin/env python

#
# gcc GTK+ Hello World
#
# build2.py
#
# Source: https://developer.gnome.org/gtk3/stable/gtk-getting-started.html
#
# Requirements:
#
#   Ubuntu: sudo apt install gcc ccache libgtk-3-dev
#
# This adds on build2.py by explicitly configuring the utilities (the values are all identical to
# the default), just to show you what is possible.
#

from ronin.cli import cli
from ronin.contexts import new_context
from ronin.gcc import configure_gcc, GccCompile, GccLink
from ronin.phases import Phase
from ronin.pkg_config import configure_pkg_config, Package
from ronin.projects import Project
from ronin.ninja import configure_ninja
from ronin.utils.paths import base_path, glob

with new_context(root_path=base_path(__file__),
                       input_path_relative=None,
                       output_path_relative='build3',
                       binary_path_relative='bin',
                       object_path_relative='obj',
                       source_path_relative='src') as ctx:

    configure_ninja(command='ninja',
                    file_name='build.ninja',
                    columns=100,
                    strict=False)
    
    configure_gcc(command='gcc',
                  ccache=True,
                  ccache_path='/usr/lib/ccache')
    
    configure_pkg_config(command='pkg-config',
                         path=None)

    project = Project('gcc GTK+ Hello World')
    extensions = [Package('gtk+-3.0')]
    
    # Compile
    compile = Phase()
    compile.executor = GccCompile()
    compile.inputs = glob('src/**/*.c')
    compile.extensions += extensions
    project.phases['compile'] = compile

    # Link
    link = Phase()
    link.executor = GccLink()
    link.inputs_from.append(compile)
    link.extensions += extensions
    link.output = 'example_1'
    project.phases['link'] = link
    
    cli(project)
