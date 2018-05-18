#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
:mod:`orion.core.io.experiment_branch_builder.py` -- Module building the
difference between a parent experience and its branching child.
========================================================================
.. module:: experiment_branch_builder
   :platform: Unix
   :synopsis: Gets a conflicting config regarding a given experiment and
   handles the solving of the different conflicts

"""

import logging
import re

from orion.core.io.space_builder import SpaceBuilder

log = logging.getLogger(__name__)


class Conflict:
    """Represent a single conflict inside the configuration"""

    def __init__(self, status, dimension):
        self.is_solved = False
        self.dimension = dimension
        self.status = status


class ExperimentBranchBuilder:
    """Build a new configuration for the experiment based on parent config."""

    def __init__(self, experiment_config, conflicting_config):
        """
        Initialize the ExperimentBranchBuilder by populating a list of the conflicts inside
        the two configurations.
        """
        self.experiment_config = experiment_config
        self.conflicting_config = conflicting_config

        self.conflicts = []
        self.operations = {}
        self._operations_mapping = {'add': self._add_adaptor,
                                    'rename': self._rename_adaptor,
                                    'remove': self._remove_adaptor}

        self.special_keywords = {'~new': 'new',
                                 '~changed': 'changed',
                                 '~missing': 'missing'}

        self.commandline_keywords = {'~+': [], '~-': [], '~>': []}
        self.cl_keywords_functions = {'~+': self.add_dimension,
                                      '~-': self.remove_dimension,
                                      '~>': self.rename_dimension}
        self.cl_keywords_re = {'~+': re.compile(r'([a-zA-Z_]+)~+'),
                               '~-': re.compile(r'([a-zA-Z_]+)~-'),
                               '~>': re.compile(r'([a-zA-Z_]+)~>([a-zA-Z_]+)')}

        self._interpret_commandline()
        self._build_spaces()
        self._find_conflicts()
        self._solve_commandline_conflicts()

        branching_name = conflicting_config.pop('branch', None)
        if branching_name is not None:
            self.change_experiment_name(branching_name)

    def _interpret_commandline(self):
        args = self.conflicting_config['metadata']['user_args']
        for arg in args:
            for keyword in self.commandline_keywords:
                if keyword in arg:
                    self.commandline_keywords[keyword].append(arg)
                    args[args.index(arg)] = arg.replace(keyword, '~')

    def _build_spaces(self):
        # Remove config solving indicators for space builder
        user_args = self.conflicting_config['metadata']['user_args']
        experiment_args = self.experiment_config['metadata']['user_args']

        self.experiment_space = SpaceBuilder().build_from(experiment_args)
        self.conflicting_space = SpaceBuilder().build_from(user_args)

    def _find_conflicts(self):
        # Loop through the conflicting space and identify problematic dimensions
        for dim in self.conflicting_space.values():
            # If the name is inside the space but not the value the dimensions has changed
            if dim.name in self.experiment_space:
                if dim not in self.experiment_space.values():
                    self.conflicts.append(Conflict('changed', dim))
            # If the name does not exist, it is a new dimension
            else:
                self.conflicts.append(Conflict('new', dim))

        # In the same vein, if any dimension of the current space is not inside
        # the conflicting space, it is missing
        for dim in self.experiment_space.values():
            if dim.name not in self.conflicting_space:
                self.conflicts.append(Conflict('missing', dim))

    def _solve_commandline_conflicts(self):
        for keyword in self.commandline_keywords:
            for dimension in self.commandline_keywords[keyword]:
                value = self.cl_keywords_re[keyword].findall(dimension)
                self.cl_keywords_functions[keyword](*value)

    # API section

    def change_experiment_name(self, arg):
        """Make sure arg is a valid, non-conflicting name, and change the experiment's name to it"""
        if arg != self.experiment_config['name']:
            self.conflicting_config['name'] = arg

    def add_dimension(self, name):
        """Add `name` dimension to the solved conflicts list"""
        self._do_basic(name, ['new', 'changed'], 'add')

    def remove_dimension(self, name):
        """Remove `name` from the configuration and marks conflict as solved"""
        self._do_basic(name, ['missing'], 'remove')

    def _do_basic(self, name, status, operation):
        for _name in self._get_names(name, status):
            conflict = self._mark_as_solved(_name, status)
            self._put_operation(operation, (conflict))

    def reset_dimension(self, arg):
        status = ['missing', 'new', 'changed']
        for _name in self._get_names(arg, status):
            conflict = self._mark_as(arg, status, False)
            self._remove_from_operations(conflict)

    def rename_dimension(self, args):
        """Change the name of old dimension to new dimension"""
        old, new = args

        old_index, missing_conflicts = self._assert_has_status(old, 'missing')
        new_index, new_conflicts = self._assert_has_status(new, 'new')

        old_conflict = missing_conflicts[old_index]
        new_conflict = new_conflicts[new_index]

        old_conflict.is_solved = True
        new_conflict.is_solved = True
        self._put_operation('rename', (old_conflict, new_conflict))

    def get_dimension_conflict(self, name):
        prefixed_name = '/' + name
        index = list(map(lambda c: c.dimension.name, self.conflicts)).index(prefixed_name)
        return self.conflicts[index]

    def get_old_dimension_value(self, name):
        """Return the dimension from the parent experiment space"""
        if name in self.experiment_space:
            return self.experiment_space[name]

        return None

    def filter_conflicts_with_solved_state(self, wants_solved=False):
        return self.filter_conflicts(lambda c: c.is_solved is wants_solved)

    def filter_conflicts_with_status(self, status):
        return self.filter_conflicts(lambda c: c.status in status)

    def filter_conflicts(self, filter_function):
        return filter(filter_function, self.conflicts)

    def create_adaptors(self):
        adaptors = []
        for operation in self.operations:
            for conflict in self.operations[operation]:
                adaptors.append(self._operations_mapping[operation](conflict))

    # Helper functions
    def _get_names(self, name, status):
        args = name.split(' ')
        names = []

        for arg in args:
            if arg in self.special_keywords:
                self._extend_special_keywords(arg, names)
            elif '*' in arg:
                self._extend_wildcard(arg, names, status)
            else:
                names = [arg]

        return names

    def _extend_special_keywords(self, arg, names):
        names.extend(list(map(lambda c: c.dimension.name[1:],
                          self.filter_conflicts_with_status([
                              self.special_keywords[arg]]))))

    def _extend_wildcard(self, arg, names, status):
        prefix = '/' + arg.split('*')[0]
        filtered_conflicts = self.filter_conflicts(lambda c:
                                                   c.dimension.name
                                                   .startswith(prefix) and c.status in status)
        names.extend(list(map(lambda c: c.dimension.name[1:], filtered_conflicts)))

    def _mark_as_solved(self, name, status):
        return self._mark_as(name, status, True)

    def _mark_as(self, name, status, is_solved):
        index, conflicts = self._assert_has_status(name, status)
        conflict = conflicts[index]
        conflict.is_solved = is_solved

        return conflict

    def _assert_has_status(self, name, status):
        prefixed_name = '/' + name
        conflicts = list(self.filter_conflicts_with_status(status))
        index = list(map(lambda c: c.dimension.name, conflicts)).index(prefixed_name)

        return index, conflicts

    def _put_operation(self, operation_name, args):
        if operation_name not in self.operations:
            self.operations[operation_name] = []

        if args not in self.operations[operation_name]:
            self.operations[operation_name].append(args)

    def _remove_from_operations(self, arg):
        for operation in self.operations:
            if operation == 'rename':
                for value in self.operations[operation]:
                    old, new = value
                    if arg in value:
                        old.is_solved = False
                        new.is_solved = False
                        self.operations[operation].remove(value)

            elif arg in self.operations[operation]:
                arg.is_solved = False
                self.operations[operation].remove(arg)

    # TODO Create Adaptor instances
    def _add_adaptor(self, conflict):
        if conflict.status == 'changed':
            pass
        else:
            pass

    def _rename_adaptor(self, conflict):
        pass

    def _remove_adaptor(self, conflict):
        pass
