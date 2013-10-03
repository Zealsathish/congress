#! /usr/bin/python

import collections
import logging
import compile
import unify
import copy

class Tracer(object):
    def __init__(self):
        self.expressions = []
    def trace(self, table):
        self.expressions.append(table)
    def is_traced(self, table):
        return table in self.expressions or '*' in self.expressions
    def log(self, table, msg, depth=0):
        if self.is_traced(table):
            logging.debug("{}{}".format(("| " * depth), msg))


class CongressRuntime (Exception):
    pass


##############################################################################
## Events
##############################################################################

class EventQueue(object):
    def __init__(self):
        self.queue = collections.deque()

    def enqueue(self, event):
        # should eliminate duplicates (or refcount dups)
        self.queue.append(event)

    def dequeue(self):
        return self.queue.popleft()

    def __len__(self):
        return len(self.queue)

    def __str__(self):
        return "[" + ",".join([str(x) for x in self.queue]) + "]"

class Event(object):
    def __init__(self, table=None, tuple=None, insert=True, proofs=None):
        self.table = table
        self.tuple = Database.DBTuple(tuple, proofs=proofs)
        self.insert = insert
        logging.debug("EV: created event {}".format(str(self)))

    def is_insert(self):
        return self.insert

    def __str__(self):
        if self.is_insert():
            sign = '+'
        else:
            sign = '-'
        return "{}{}({})".format(self.table, sign, str(self.tuple))

    def atom(self):
        return compile.Atom.create_from_table_tuple(self.table, self.tuple.tuple)

##############################################################################
## Database
##############################################################################

class Database(object):
    class Proof(object):
        def __init__(self, binding, rule):
            self.binding = binding
            self.rule = rule

        def __str__(self):
            return "apply({}, {})".format(str(self.binding), str(self.rule))

        def __eq__(self, other):
            result = (self.binding == other.binding and
                      self.rule == other.rule)
            # logging.debug("Pf: Comparing {} and {}: {}".format(
            #     str(self), str(other), result))
            # logging.debug("Pf: {} == {} is {}".format(
            #     str(self.binding), str(other.binding), self.binding == other.binding))
            # logging.debug("Pf: {} == {} is {}".format(
            #     str(self.rule), str(other.rule), self.rule == other.rule))
            return result

    class ProofCollection(object):
        def __init__(self, proofs):
            self.contents = list(proofs)

        def __str__(self):
            return '{' + ",".join(str(x) for x in self.contents) + '}'

        def __isub__(self, other):
            if other is None:
                return
            # logging.debug("PC: Subtracting {} and {}".format(str(self), str(other)))
            remaining = []
            for proof in self.contents:
                if proof not in other.contents:
                    remaining.append(proof)
            self.contents = remaining
            return self

        def __ior__(self, other):
            if other is None:
                return
            # logging.debug("PC: Unioning {} and {}".format(str(self), str(other)))
            for proof in other.contents:
                # logging.debug("PC: Considering {}".format(str(proof)))
                if proof not in self.contents:
                    self.contents.append(proof)
            return self

        def __getitem__(self, key):
            return self.contents[key]

        def __len__(self):
            return len(self.contents)

        def __ge__(self, other):
            return other <= self

        def __le__(self, other):
            for proof in self.contents:
                if proof not in other.contents:
                    return False
            return True

        def __eq__(self, other):
            return self <= other and other <= self

    class DBTuple(object):
        def __init__(self, iterable, proofs=None):
            self.tuple = tuple(iterable)
            if proofs is None:
                proofs = []
            self.proofs = Database.ProofCollection(proofs)

        def __eq__(self, other):
            return self.tuple == other.tuple

        def __str__(self):
            return str(self.tuple) + str(self.proofs)

        def __len__(self):
            return len(self.tuple)

        def __getitem__(self, index):
            return self.tuple[index]

        def __setitem__(self, index, value):
            self.tuple[index] = value

        def match(self, atom, binding):
            logging.debug("Checking if tuple {} matches atom {} with binding {}".format(
                str(self), str(atom), str(binding)))
            if len(self.tuple) != len(atom.arguments):
                return None
            new_binding = {}
            for i in xrange(0, len(atom.arguments)):
                # variable
                if atom.arguments[i].is_variable():
                    if atom.arguments[i].name in binding:
                        if binding[atom.arguments[i].name] != self.tuple[i]:
                            return None
                    else:
                        new_binding[atom.arguments[i].name] = self.tuple[i]
                # constant
                else:
                    if atom.arguments[i].name != self.tuple[i]:
                        return None
            logging.debug("Check succeeded with binding {}".format(str(new_binding)))
            return new_binding

    def __init__(self):
        self.data = {}
        self.tracer = Tracer()

    def __str__(self):
        def hash2str (h):
            s = "{"
            s += ", ".join(["{} : {}".format(str(key), str(h[key]))
                  for key in h])
            return s

        def hashlist2str (h):
            strings = []
            for key in h:
                s = "{} : ".format(key)
                s += '['
                s += ', '.join([str(val) for val in h[key]])
                s += ']'
                strings.append(s)
            return '{' + ", ".join(strings) + '}'

        return hashlist2str(self.data)

    def __eq__(self, other):
        return self.data == other.data

    def __sub__(self, other):
        def add_tuple(table, dbtuple):
            new = [table]
            new.extend(dbtuple.tuple)
            results.append(new)

        results = []
        for table in self.data:
            if table not in other.data:
                for dbtuple in self.data[table]:
                    add_tuple(table, dbtuple)
            else:
                for dbtuple in self.data[table]:
                    if dbtuple not in other.data[table]:
                        add_tuple(table, dbtuple)
        return results

    def __getitem__(self, key):
        # KEY must be a tablename
        return self.data[key]

    def table_names(self):
        return self.data.keys()

    def log(self, table, msg, depth=0):
        self.tracer.log(table, "DB: " + msg, depth)

    def is_noop(self, event):
        """ Returns T if EVENT is a noop on the database. """
        # insert/delete same code but with flipped return values
        # Code below is written as insert, except noop initialization.
        if event.is_insert():
            noop = True
        else:
            noop = False
        if event.table not in self.data:
            return not noop
        event_data = self.data[event.table]
        for dbtuple in event_data:
            # event.tuple is a dbtuple (and == only checks their .tuple fields)
            if dbtuple == event.tuple and event.tuple.proofs <= dbtuple.proofs:
                return noop
        return not noop

    def select(self, query):
        #logging.debug("DB: select({})".format(str(query)))
        if isinstance(query, compile.Atom):
            bindings = self.top_down_eval([query], 0, {})
            return [query.plug(binding) for binding in bindings]
        elif isinstance(query, compile.Rule):
            bindings = self.top_down_eval(query.body, 0, {})
            return [query.plug(binding) for binding in bindings]
        else:
            assert False, "Queries must be atoms or rules"

    def explain(self, atom):
        if atom.table not in self.data or not atom.is_ground():
            return self.ProofCollection()
        args = tuple([x.name for x in atom.arguments])
        for dbtuple in self.data[atom.table]:
            if dbtuple.tuple == args:
                return dbtuple.proofs

    def top_down_eval(self, literals, literal_index, binding):
        """ Compute all instances of LITERALS (from LITERAL_INDEX and above) that
            are true in the Database (after applying the dictionary binding
            BINDING to LITERALS).  Returns a list of dictionary bindings. """
        if literal_index > len(literals) - 1:
            return [binding]
        lit = literals[literal_index]
        self.log(lit.table, ("Top_down_eval(literals={}, literal_index={}, "
                   "bindings={})").format(
                    "[" + ",".join(str(x) for x in literals) + "]",
                    literal_index,
                    str(binding)),
                   depth=literal_index)
        # assume that for negative literals, all vars are bound at this point
        # if there is a match, data_bindings will contain at least one binding
        #     (possibly including the empty binding)
        data_bindings = self.matches(lit, binding)
        self.log(lit.table, "data_bindings: " + str(data_bindings), depth=literal_index)
        # if not negated, empty data_bindings means failure
        if len(data_bindings) == 0 :
            return []

        results = []
        for data_binding in data_bindings:
            # add new binding to current binding
            binding.update(data_binding)
            if literal_index == len(literals) - 1:  # last element
                results.append(dict(binding))  # need to copy
            else:
                results.extend(self.top_down_eval(literals, literal_index + 1,
                    binding))
            # remove new binding from current bindings
            for var in data_binding:
                del binding[var]
        self.log(lit.table, "Top_down_eval return value: {}".format(
            '[' + ", ".join([str(x) for x in results]) + ']'), depth=literal_index)

        return results

    def matches(self, literal, binding):
        """ Returns a list of binding lists for the variables in LITERAL
            not bound in BINDING.  If LITERAL is negative, returns
            either [] meaning the lookup failed or [{}] meaning the lookup
            succeeded; otherwise, returns one binding list for each tuple in
            the database matching LITERAL under BINDING. """
        # slow for negation--should stop at first match, not find all of them
        matches = self.matches_atom(literal, binding)
        if literal.is_negated():
            if len(matches) > 0:
                return []
            else:
                return [{}]
        else:
            return matches

    def matches_atom(self, atom, binding):
        """ Returns a list of binding lists for the variables in ATOM
            not bound in BINDING: one binding list for each tuple in
            the database matching ATOM under BINDING. """
        if atom.table not in self.data:
            return []
        result = []
        for tuple in self.data[atom.table]:
            logging.debug("Matching database tuple {}".format(str(tuple)))
            new_binding = tuple.match(atom, binding)
            if new_binding is not None:
                result.append(new_binding)
        return result

    def insert(self, table, dbtuple):
        if not isinstance(dbtuple, Database.DBTuple):
            dbtuple = Database.DBTuple(dbtuple)
        self.log(table, "Inserting table {} tuple {}".format(
            table, str(dbtuple)))
        if table not in self.data:
            self.data[table] = [dbtuple]
            # self.log(table, "First tuple in table {}".format(table))
        else:
            # self.log(table, "Not first tuple in table {}".format(table))
            for existingtuple in self.data[table]:
                assert(existingtuple.proofs is not None)
                if existingtuple.tuple == dbtuple.tuple:
                    # self.log(table, "Found existing tuple: {}".format(
                    #     str(existingtuple)))
                    assert(existingtuple.proofs is not None)
                    existingtuple.proofs |= dbtuple.proofs
                    # self.log(table, "Updated tuple: {}".format(str(existingtuple)))
                    assert(existingtuple.proofs is not None)
                    return
            self.data[table].append(dbtuple)


    def delete(self, table, dbtuple):
        if not isinstance(dbtuple, Database.DBTuple):
            dbtuple = Database.DBTuple(dbtuple)
        self.log(table, "Deleting table {} tuple {} from DB".format(
            table, str(dbtuple)))
        if table not in self.data:
            return
        for i in xrange(0, len(self.data[table])):
            existingtuple = self.data[table][i]
            self.log(table, "Checking tuple {}".format(str(existingtuple)))
            if existingtuple.tuple == dbtuple.tuple:
                existingtuple.proofs -= dbtuple.proofs
                if len(existingtuple.proofs) == 0:
                    del self.data[table][i]
                return

##############################################################################
## Logical Building Blocks
##############################################################################

class Proof(object):
    """ A single proof. Differs semantically from Database's
    Proof in that this verison represents a proof that spans rules,
    instead of just a proof for a single rule. """
    def __init__(self, root, children):
        self.root = root
        self.children = children

    def __str__(self):
        return self.str_tree(0)

    def str_tree(self, depth):
        s = " " * depth
        s += str(self.root)
        s += "\n"
        for child in self.children:
            s += child.str_tree(depth + 1)
        return s

class DeltaRule(object):
    def __init__(self, trigger, head, body, original):
        self.trigger = trigger  # atom
        self.head = head  # atom
        self.body = body  # list of atoms with is_negated()
        self.original = original # Rule from which derived

    def __str__(self):
        return "<trigger: {}, head: {}, body: {}>".format(
            str(self.trigger), str(self.head), [str(lit) for lit in self.body])

    def __eq__(self, other):
        return (self.trigger == other.trigger and
                self.head == other.head and
                len(self.body) == len(other.body) and
                all(self.body[i] == other.body[i]
                        for i in xrange(0, len(self.body))))


##############################################################################
## Theories
##############################################################################

def new_BiUnifier():
    return unify.BiUnifier(lambda (index):
        compile.Variable("x" + str(index)))

class NonrecursiveRuleTheory(object):
    """ A non-recursive collection of Rules. """

    def __init__(self, rules=None):
        # dictionary from table name to list of rules with that table in head
        self.contents = {}
        # list of other theories that are implicitly included in this one
        self.includes = []
        if rules is not None:
            for rule in rules:
                self.insert(rule)

    def __str__(self):
        return str(self.contents)

    def insert(self, rule):
        if isinstance(rule, compile.Atom):
            rule = compile.Rule(rule, [], rule.location)
        table = rule.head.table
        if table in self.contents:
            if rule not in self.contents[table]:  # eliminate dups
                self.contents[table].append(rule)
        else:
            self.contents[table] = [rule]

    def delete(self, rule):
        if isinstance(rule, compile.Atom):
            rule = compile.Rule(rule, [], rule.location)
        table = rule.head.table
        if table in self.contents:
            self.contents[table].remove(rule)

    class TopDownContext(object):
        """ Struct for storing the search state of top-down evaluation """
        def __init__(self, literals, literal_index, binding, context, depth):
            self.literals = literals
            self.literal_index = literal_index
            self.binding = binding
            self.previous = context
            self.depth = depth

        def __str__(self):
            return ("TopDownContext<literals={}, literal_index={}, binding={}, "
                    "previous={}, depth={}>").format(
                "[" + ",".join([str(x) for x in self.literals]) + "]",
                str(self.literal_index), str(self.binding),
                str(self.previous), str(self.depth))

    class TopDownCaller(object):
        """ Struct for storing info about the original caller of top-down
        evaluation. QUERY is the initial query requested, ANSWERS
        is populated by top-down evaluation: it is the list of QUERY
        instances that the search process proved true.
        MAX_ANSWERS is the largest number of answers top-down should
        find; setting to None finds all."""
        def __init__(self, query, binding, max_answers=1):
            self.query = query
            self.binding = binding
            self.answers = []
            self.max_answers = max_answers

        def __str__(self):
            return "TopDownCaller<query={}, binding={}, answers={}>".format(
                str(self.query), str(self.binding), str(self.answers))

    def select(self, query, max_answers=1):
        """ Return tuples in which QUERY is true. """
        # No unit test for MAX_ANSWERS--don't yet support it in the Runtime
        #   May not even be necessary.
        assert (isinstance(query, compile.Atom) or
                isinstance(query, compile.Rule)), "Query must be atom/rule"
        if isinstance(query, compile.Atom):
            literals = [query]
        else:
            literals = query.body
        unifier = new_BiUnifier()
        context = self.TopDownContext(literals, 0, unifier, None, 0)
        caller = self.TopDownCaller(query, unifier, max_answers=max_answers)
        self.top_down_eval(context, caller)
        logging.debug(caller.answers)
        if len(caller.answers) > 0:
            logging.debug("Found answer {}".format(
                "[" + ",".join([str(x) for x in caller.answers]) + "]"))
            return [str(x) for x in caller.answers]
        else:
            return []

    # def match(atom1, atom2):
    #     """ Return Unifier, if it exists, that when applied to
    #     ATOM1 results in the ground ATOM2. """
    #     if len(atom1.arguments) != len(atom2.arguments):
    #         return None
    #     assert all(not arg.is_variable() for arg in atom2.arguments), \
    #         "Match requires ATOM2 have no variables"
    #     binding = Unifier()
    #     for i in xrange(0, len(atom1.arguments)):
    #         arg = atom1.arguments[i]
    #         if arg.is_variable():
    #             if arg.name in binding:
    #                 oldval = binding.apply(arg.name)
    #                 if oldval != atom2.arguments[i]:
    #                     return None
    #             else:
    #                 binding.add(arg.name, atom2.arguments[i])
    #     return binding

    def return_true(*args):
        return True

    def abduce(self, query, abducibles, consistency=return_true):
        """ Compute a collection of atoms with ABDUCIBLES in the head
            that when added to SELF makes query QUERY true (for some
            instance of QUERY). """
        assert False, "Not yet implemented"

    def top_down_eval(self, context, caller):
        """ Compute all instances of LITERALS (from LITERAL_INDEX and above)
            that are true according to the theory (after applying the
            unifier BINDING to LITERALS).  Returns False or an answer. """
        # no recursion, ever; this style of algorithm will never halt
        #    on recursive rules
        # no negation/recursion/included theories for now.
        return self.top_down_th(context, caller)

    def top_down_th(self, context, caller):
        """ Top-down evaluation for just the rules in SELF.CONTENTS. """
        # logging.debug("top_down_th({})".format(str(context)))
        depth = context.depth
        binding = context.binding

        if context.literal_index > len(context.literals) - 1:
            return True
        lit = context.literals[context.literal_index]
        self.top_down_call(lit, binding, depth)
        if lit.table not in self.contents:
            return self.top_down_fail(lit, binding, depth)
        for rule in self.contents[lit.table]:
            unifier = new_BiUnifier()
            # Prefer to bind vars in rule head
            undo = unify.bi_unify_atoms(rule.head, unifier, lit, binding)
            # self.log(lit.table, "Rule: {}, Unifier: {}, Undo: {}".format(
            #     str(rule), str(unifier), str(undo)))
            if undo is None:  # no unifier
                continue
            if len(rule.body) == 0:
                if self.top_down_th_finish(context, caller):
                    unify.undo_all(undo)
                    return True
                else:
                    unify.undo_all(undo)
            else:
                new_context = self.TopDownContext(rule.body, 0,
                    unifier, context, depth + 1)
                if self.top_down_eval(new_context, caller):
                    unify.undo_all(undo)
                    return True
                else:
                    unify.undo_all(undo)
        return self.top_down_fail(lit, binding, depth)

    def top_down_th_finish(self, context, caller):
        """ Helper that is called once top_down successfully completes
            a proof for a literal.  Handles (i) continuing search
            for those literals still requiring proofs within CONTEXT,
            (ii) adding solutions to CALLER once all needed proofs have
            been found, and (iii) printing out Redo/Exit during tracing.
            Returns True if the search is finished and False otherwise.
            Temporary, transparent modification of CONTEXT."""
        if context is None:
            # plug now before we undo the bindings
            caller.answers.append(caller.query.plug_new(caller.binding))
            # return True iff the search is finished.
            if caller.max_answers is None:
                return False
            return len(caller.answers) >= caller.max_answers
        else:
            self.top_down_exit(context.literals[context.literal_index],
                context.binding, context.depth)
            # continue the search
            if context.literal_index < len(context.literals) - 1:
                context.literal_index += 1
                finished = self.top_down_eval(context, caller)
                context.literal_index -= 1  # in case answer is False
            else:
                finished = self.top_down_th_finish(context.previous, caller)
            # return search result (after printing a Redo if failure)
            if not finished:
                self.top_down_redo(context.literals[context.literal_index],
                    context.binding, context.depth)
            return finished

    def top_down_call(self, literal, binding, depth):
        self.log(literal.table, "{}Call: {} with {}".format("| "*depth,
            literal.plug_new(binding), str(binding)))

    def top_down_exit(self, literal, binding, depth):
        self.log(literal.table, "{}Exit: {} with {}".format("| "*depth,
            literal.plug_new(binding), str(binding)))

    def top_down_fail(self, literal, binding, depth):
        self.log(literal.table, "{}Fail: {} with {}".format("| "*depth,
            literal.plug_new(binding), str(binding)))
        return False

    def top_down_redo(self, literal, binding, depth):
        self.log(literal.table, "{}Redo: {} with {}".format("| "*depth,
            literal.plug_new(binding), str(binding)))
        return False

    def log(self, table, msg, depth=0):
        self.tracer.log(table, "RuleTh: " + msg, depth)

class DeltaRuleTheory (object):
    """ A collection of DeltaRules. """
    def __init__(self, rules=None):
        # dictionary from table name to list of rules with that table as trigger
        self.contents = {}
        # list of theories implicitly included in this one
        self.includes = []
        # dictionary from table name to number of rules with that table in head
        self.views = {}
        if rules is not None:
            for rule in rules:
                self.insert(rule)

    def insert(self, delta):
        if delta.head.table in self.views:
            self.views[delta.head.table] += 1
        else:
            self.views[delta.head.table] = 1

        if delta.trigger.table not in self.contents:
            self.contents[delta.trigger.table] = [delta]
        else:
            self.contents[delta.trigger.table].append(delta)

    def delete(self, delta):
        if delta.head.table in self.views:
            self.views[delta.head.table] -= 1
            if self.views[delta.head.table] == 0:
                del self.views[delta.head.table]
        if delta.trigger.table not in self.contents:
            return
        self.contents[delta.trigger.table].remove(delta)

    def modify(self, delta, is_insert):
        if is_insert is True:
            return self.insert(delta)
        else:
            return self.delete(delta)

    def __str__(self):
        return str(self.contents)

    def rules_with_trigger(self, table):
        if table not in self.contents:
            return []
        else:
            return self.contents[table]

    def is_view(self, x):
        return x in self.views

class MaterializedRuleTheory(object):
    """ A theory that stores the table contents explicitly.
        Recursive rules are allowed. """

    def __init__(self):
        # queue of events left to process
        self.queue = EventQueue()
        # collection of all tables
        self.database = Database()
        # tracer object
        self.tracer = Tracer()
        # rules that dictate how database changes in response to events
        self.delta_rules = DeltaRuleTheory()

    ############### External Interface ###############

    def select(self, query):
        # should generalize to at least a conjunction of atoms.
        #   Need to change compiler a bit, but runtime should be fine.
        assert (isinstance(query, compile.Atom) or
                isinstance(query, compile.Rule)), \
             "Only have support for atoms"
        return self.database.select(query)

    def insert(self, formula):
        return self.modify(formula, is_insert=True)

    def delete(self, formula):
        return self.modify(formula, is_insert=False)

    def explain(self, query):
        assert isinstance(query, compile.Atom), "Only have support for atoms"
        return self.explain_aux(query, 0)


    ############### Interface implementation ###############

    def log(self, table, msg, depth=0):
        self.tracer.log(table, "MRT: " + msg, depth)

    def explain_aux(self, query, depth):
        self.log(query.table, "Explaining {}".format(str(query)), depth)
        if query.is_negated():
            return Proof(query, [])
        # grab first local proof, since they're all equally good
        localproofs = self.database.explain(query)
        if len(localproofs) == 0:   # base fact
            return Proof(query, [])
        localproof = localproofs[0]
        rule_instance = localproof.rule.plug(localproof.binding)
        subproofs = []
        for lit in rule_instance.body:
            subproof = self.explain_aux(lit, depth + 1)
            if subproof is None:
                return None
            subproofs.append(subproof)
        return Proof(query, subproofs)

    def modify(self, formula, is_insert=True):
        """ Event handler for arbitrary insertion/deletion (rules and facts). """
        if formula.is_atom():
            assert not self.is_view(formula.table), \
                "Cannot directly modify tables computed from other tables"
            args = tuple([arg.name for arg in formula.arguments])
            self.modify_tables_with_tuple(
                formula.table, args, is_insert=is_insert)
            return None
        else:
            self.modify_tables_with_rule(
                formula, is_insert=is_insert)
            for delta_rule in compile.compute_delta_rules([formula]):
                self.delta_rules.modify(delta_rule, is_insert=is_insert)
            return None

    def modify_tables_with_rule(self, rule, is_insert):
        """ Add rule (not a DeltaRule) to collection and update
            tables as appropriate. """
        # don't have separate queue since inserting/deleting a rule doesn't generate any
        #   new rule insertion/deletion events
        bindings = self.database.top_down_eval(rule.body, 0, {})
        self.log(None, "new bindings after top-down: {}".format(
            ",".join([str(x) for x in bindings])))
        self.process_new_bindings(bindings, rule.head, is_insert, rule)
        self.process_queue()

    def modify_tables_with_tuple(self, table, row, is_insert):
        """ Event handler for a tuple insertion/deletion.
        TABLE is the name of a table (a string).
        TUPLE is a Python tuple.
        IS_INSERT is True or False."""
        if is_insert:
            text = "Inserting into queue"
        else:
            text = "Deleting from queue"
        self.log(table, "{}: table {} with tuple {}".format(
            text, table, str(row)))
        if not isinstance(row, Database.DBTuple):
            row = Database.DBTuple(row)
        self.log(table, "{}: table {} with tuple {}".format(
            text, table, str(row)))
        self.queue.enqueue(Event(table, row, insert=is_insert))
        self.process_queue()

    ############### Data manipulation ###############

    def process_queue(self):
        """ Toplevel data evaluation routine. """
        while len(self.queue) > 0:
            event = self.queue.dequeue()
            if self.database.is_noop(event):
                self.log(event.table, "is noop")
                continue
            self.log(event.table, "is not noop")
            if event.is_insert():
                self.propagate(event)
                self.database.insert(event.table, event.tuple)
            else:
                self.propagate(event)
                self.database.delete(event.table, event.tuple)

    def propagate(self, event):
        """ Computes events generated by EVENT and the DELTA_RULES,
            and enqueues them. """
        self.log(event.table, "Processing event: {}".format(str(event)))
        applicable_rules = self.delta_rules.rules_with_trigger(event.table)
        if len(applicable_rules) == 0:
            self.log(event.table, "No applicable delta rule")
        for delta_rule in applicable_rules:
            self.propagate_rule(event, delta_rule)

    def propagate_rule(self, event, delta_rule):
        """ Compute and enqueue new events generated by EVENT and DELTA_RULE. """
        self.log(event.table, "Processing event {} with rule {}".format(
            str(event), str(delta_rule)))

        # compute tuples generated by event (either for insert or delete)
        # print "event: {}, event.tuple: {}, event.tuple.rawtuple(): {}".format(
        #     str(event), str(event.tuple), str(event.tuple.raw_tuple()))
        binding_list = match(event.tuple, delta_rule.trigger)
        if binding_list is None:
            return
        self.log(event.table,
            "binding_list for event-tuple and delta_rule trigger: {}".format(
                str(binding_list)))
        new_bindings = self.database.top_down_eval(delta_rule.body, 0, binding_list)
        self.log(event.table, "new bindings after top-down: {}".format(
            ",".join([str(x) for x in new_bindings])))

        if delta_rule.trigger.is_negated():
            insert_delete = not event.insert
        else:
            insert_delete = event.insert
        self.process_new_bindings(new_bindings, delta_rule.head, insert_delete,
            delta_rule.original)

    def is_view(self, x):
        return self.delta_rules.is_view(x)

    def process_new_bindings(self, bindings, atom, insert, original_rule):
        """ For each of BINDINGS, apply to ATOM, and enqueue it as an insert if
            INSERT is True and as a delete otherwise. """
        # for each binding, compute generated tuple and group bindings
        #    by the tuple they generated
        new_tuples = {}
        for binding in bindings:
            new_tuple = tuple(plug(atom, binding))
            if new_tuple not in new_tuples:
                new_tuples[new_tuple] = []
            new_tuples[new_tuple].append(Database.Proof(
                binding, original_rule))
        self.log(atom.table, "new tuples generated: {}".format(
            ", ".join([str(x) for x in new_tuples])))

        # enqueue each distinct generated tuple, recording appropriate bindings
        for new_tuple in new_tuples:
            # self.log(event.table,
            #     "new_tuple {}: {}".format(str(new_tuple), str(new_tuples[new_tuple])))
            # Only enqueue if new data.
            # Putting the check here is necessary to support recursion.
            self.queue.enqueue(Event(table=atom.table,
                                     tuple=new_tuple,
                                     proofs=new_tuples[new_tuple],
                                     insert=insert))


##############################################################################
## Runtime
##############################################################################

class Runtime (object):
    """ Runtime for the Congress policy language.  Only have one instantiation
        in practice, but using a class is natural and useful for testing. """
    # Names of theories
    CLASSIFY_THEORY = "classification"
    SERVICE_THEORY = "service"
    ACTION_THEORY = "action"

    def __init__(self):

        # tracer object
        self.tracer = Tracer()
        # collection of theories
        self.theory = {}
        self.theory[self.CLASSIFY_THEORY] = MaterializedRuleTheory()
        self.theory[self.SERVICE_THEORY] = NonrecursiveRuleTheory()
        self.theory[self.ACTION_THEORY] = NonrecursiveRuleTheory()
        # Service/Action theories build upon Classify theory
        self.theory[self.SERVICE_THEORY].includes.append(
            self.theory[self.CLASSIFY_THEORY])
        self.theory[self.ACTION_THEORY].includes.append(
            self.theory[self.CLASSIFY_THEORY])

    def log(self, table, msg, depth=0):
        self.tracer.log(table, "RT: " + msg, depth)

    ############### External interface ###############
    def get_target(self, name):
        if name is None:
            name = self.CLASSIFY_THEORY
        assert name in self.theory, "Unknown target {}".format(name)
        return self.theory[name]

    def load_file(self, filename, target=None):
        """ Compile the given FILENAME and insert each of the statements
            into the runtime. """
        compiler = compile.get_compiled([filename])
        for formula in compiler.theory:
            self.insert(formula, target=target)

    def select(self, query, target=None):
        """ Event handler for arbitrary queries. Returns the set of
            all instantiated QUERY that are true. """
        if isinstance(query, basestring):
            return self.select_string(query, self.get_target(target))
        elif isinstance(query, tuple):
            return self.select_tuple(query, self.get_target(target))
        else:
            return self.select_obj(query, self.get_target(target))

    # Maybe implement one day
    # def select_if(self, query, temporary_data):
    #     """ Event handler for hypothetical queries.  Returns the set of
    #     all instantiated QUERYs that would be true IF
    #     TEMPORARY_DATA were true. """
    #     if isinstance(query, basestring):
    #         return self.select_if_string(query, temporary_data)
    #     else:
    #         return self.select_if_obj(query, temporary_data)

    def explain(self, query, target=None):
        """ Event handler for explanations.  Given a ground query, return
            a single proof that it belongs in the database. """
        if isinstance(query, basestring):
            return self.explain_string(query, self.get_target(target))
        elif isinstance(query, tuple):
            return self.explain_tuple(query, self.get_target(target))
        else:
            return self.explain_obj(query, self.get_target(target))

    def insert(self, formula, target=None):
        """ Event handler for arbitrary insertion (rules and facts). """
        if isinstance(formula, basestring):
            return self.insert_string(formula, self.get_target(target))
        elif isinstance(formula, tuple):
            return self.insert_tuple(formula, self.get_target(target))
        else:
            return self.insert_obj(formula, self.get_target(target))

    def delete(self, formula, target=None):
        """ Event handler for arbitrary deletion (rules and facts). """
        if isinstance(formula, basestring):
            return self.delete_string(formula, self.get_target(target))
        elif isinstance(formula, tuple):
            return self.delete_tuple(formula, self.get_target(target))
        else:
            return self.delete_obj(formula, self.get_target(target))

    ############### Internal interface ###############
    ## Only arguments allowed to be strings are suffixed with _string
    ## All other arguments are instances of Theory, Atom, etc.

    def select_obj(self, query, theory):
        return theory.select(query)

    def select_string(self, policy_string, theory):
        def str_tuple_atom (atom):
            s = atom[0]
            s += '('
            s += ', '.join([str(x) for x in atom[1:]])
            s += ')'
            return s
        c = compile.get_compiled(['--input_string', policy_string])
        assert len(c.theory) == 1, \
                "Queries can have only 1 statement: {}".format(
                    [str(x) for x in c.theory])
        results = self.select_obj(c.theory[0], theory)
        return " ".join([str(x) for x in results])

    def select_tuple(self, tuple, theory):
        return self.select_obj(self.tuple_to_atom(tuple), theory)

    def explain_obj(self, query, theory):
        return theory.explain(query)

    def explain_string(self, query_string, theory):
        c = compile.get_compiled([query_string, '--input_string'])
        assert len(c.theory) == 1, "Queries can have only 1 statement"
        assert c.theory[0].is_atom(), "Queries must be atomic"
        results = self.explain_obj(c.theory[0], theory)
        return str(results)

    def explain_tuple(self, tuple, theory):
        self.explain_obj(self.tuple_to_atom(tuple), theory)

    def insert_obj(self, formula, theory):
        return theory.insert(formula)

    def insert_string(self, policy_string, theory):
        c = compile.get_compiled([policy_string, '--input_string'])
        for formula in c.theory:
            #logging.debug("Parsed {}".format(str(formula)))
            self.insert_obj(formula, theory)

    def insert_tuple(self, tuple, theory):
        self.insert_obj(self.tuple_to_atom(tuple), theory)

    def delete_obj(self, formula, theory):
        theory.delete(formula)

    def delete_string(self, policy_string, theory):
        c = compile.get_compiled([policy_string, '--input_string'])
        for formula in c.theory:
            self.delete_obj(formula, theory)

    def delete_tuple(self, tuple, theory):
        self.delete_obj(self.tuple_to_atom(tuple), theory)

    ############### Helpers ###############
    def tuple_to_atom(self, tuple):
        table = tuple[0]
        args = [compile.Term.create_from_python(arg) for arg in tuple[1:]]
        return compile.Atom(table, args)

def plug(atom, binding, withtable=False):
    """ Returns a tuple representing the arguments to ATOM after having
        applied BINDING to the variables in ATOM. """
    if withtable is True:
        result = [atom.table]
    else:
        result = []
    for i in xrange(0, len(atom.arguments)):
        if atom.arguments[i].is_variable() and atom.arguments[i].name in binding:
            result.append(binding[atom.arguments[i].name])
        else:
            result.append(atom.arguments[i].name)
    return tuple(result)

def match(tuple, atom):
    """ Returns a binding dictionary that when applied to ATOM's arguments
        gives exactly TUPLE, or returns None if no such binding exists. """
    if len(tuple) != len(atom.arguments):
        return None
    binding = {}
    for i in xrange(0, len(tuple)):
        arg = atom.arguments[i]
        if arg.is_variable():
            if arg.name in binding:
                oldval = binding[arg.name]
                if oldval != tuple[i]:
                    return None
            else:
                binding[arg.name] = tuple[i]
    return binding


