'''Integrates ST2 with Eclim running in an Eclipse instance.
Enables Java completions / go to definition etc'''
import sublime_plugin
import sublime
import eclim
import re
import os


def display_error(view, error):
    view.set_status('subclim_error', "SUBCLIM ERROR: %s" % error)
    # clear error after 5 seconds
    sublime.set_timeout(
        lambda: view.erase_status('subclim_error'), 5000)


def initialize_eclim_module():
    '''Loads the eclim executable path from ST2's settings and sets it
    in the eclim module'''
    s = sublime.load_settings("Subclim.sublime-settings")
    eclim_executable = s.get("eclim_executable_location", None)
    eclim.eclim_executable = eclim_executable
# when this module is loaded (by ST2), initialize the eclim module
initialize_eclim_module()


def check_eclim(view):
    if not eclim.eclim_executable:
        display_error(view, "Eclim executable path not set, call the\
            set_eclim_path command!")
        return False
    return True


class SetEclimPath(sublime_plugin.WindowCommand):
    '''Asks the user for the path to the Eclim executable and saves it in
    ST2's prefernces'''
    def run(self):
        self.window.show_input_panel(
            "Input path to eclim executable (in your eclipse directory)",
            "/path/to/your/eclipse/eclim", self.path_entered, None, None)

    def path_entered(self, path):
        path = os.path.abspath(os.path.expanduser(path))
        s = sublime.load_settings("Subclim.sublime-settings")
        s.set("eclim_executable_location", path)
        sublime.save_settings("Subclim.sublime-settings")
        # re-initialize the eclim module with the new path
        initialize_eclim_module()


class JavaGotoDefinition(sublime_plugin.TextCommand):
    '''Asks Eclipse for the definition location and moves ST2 there if found'''

    def run(self, edit, block=False):
        if not check_eclim(self.view):
            return
        project, file = eclim.get_context(self.view.file_name())
        pos = self.view.sel()[0]
        word = self.view.word(pos)
        locations = self.call_eclim(project, file, word.a, word.size())
        locations = self.to_list(locations)

        #  one definition was found and it is in a java file -> go there
        if len(locations) == 1:
            if locations[0]['file'].endswith("java"):
                self.go_to_location(locations[0])
                return

        # we didnt return correctly, display error in statusbar
        error_msg = "Could not find definition of %s" % self.view.substr(word)
        display_error(self.view, error_msg)

    def call_eclim(self, project, file, offset, ident_len, shell=True):
        eclim.update_java_src(project, file)

        go_to_cmd = "-command java_search \
                                -n %s \
                                -f %s \
                                -o %i \
                                -e utf-8 \
                                -l %i" % (project, file,
                                            offset, ident_len)
        out = eclim.call_eclim(go_to_cmd)
        return out

    def to_list(self, locations):
        result = []

        locations = locations.splitlines()
        for l in locations:
            parts = l.split("|")
            l_def = {"file": parts[0],
                    "line": parts[1].split(" col ")[0],
                    "col": parts[1].split(" col ")[1]}
            result.append(l_def)
        return result

    def go_to_location(self, loc):
        f, l, c = loc['file'], loc['line'], loc['col']
        path = "%s:%s:%s" % (f, l, c)
        sublime.active_window().open_file(
            path, sublime.ENCODED_POSITION)


class JavaRunClass(sublime_plugin.TextCommand):
    '''Runs the current class as Java program, good for testing
    small Java-"Scripts"'''

    def run(self, edit, block=False):
        if not check_eclim(self.view):
            return
        project, file_name = eclim.get_context(self.view.file_name())
        class_name, _ = os.path.splitext(
            os.path.basename(file_name))
        package_name = self.find_package_name()
        if package_name:
            class_name = package_name + "." + class_name
        result = self.call_eclim(project, file_name, class_name)
        # print stdout of Java program to ST2's console
        print result

    def find_package_name(self):
        '''Searches the current file line by line for the
        package definition.'''
        line_regions = self.view.split_by_newlines(
            sublime.Region(0, self.view.size()))
        for line_region in line_regions:
            line = self.view.substr(line_region)
            m = re.search(r'package ([^;]*);', line)
            if m:
                return m.group(1)
        return None

    def call_eclim(self, project, file_name, class_name):
        eclim.update_java_src(project, file_name)

        go_to_cmd = "-command java \
                                -p %s \
                                -c %s" % (project, class_name)
        out = eclim.call_eclim(go_to_cmd)
        return out


class CompletionProposal(object):
    def __init__(self, name, insert=None, type="None"):
        self.name = name
        self.display = name
        if insert:
            self.insert = insert
        else:
            self.insert = name
        self.type = "None"


class JavaCompletions(sublime_plugin.EventListener):
    '''Java completion provider'''

    def on_query_completions(self, view, prefix, locations):
        if not view.match_selector(locations[0], "source.java"):
            return []
        if not check_eclim(view):
            return []

        # we need to save the view on every call to completion, or eclipse
        # wont see the changes
        view.run_command("save")

        project, file = eclim.get_context(view.file_name())
        pos = locations[0]

        proposals, with_snippets = self.to_proposals(
            self.call_eclim(project, file, pos))
        # if len(proposals) == 1:
        #     proposals.append(CompletionProposal("dummy"))
        return [(p.display, p.insert) for p in proposals]

    def call_eclim(self, project, file, offset, shell=True):
        eclim.update_java_src(project, file)
        complete_cmd = "-command java_complete \
                                -p %s \
                                -f %s \
                                -o %i \
                                -e utf-8 \
                                -l compact" % (project, file, offset)
        out = eclim.call_eclim(complete_cmd)
        return out

    def to_proposals(self, eclim_output):
        results = []
        with_snippets = False
        for l in eclim_output.split("\n"):
            if not l:
                continue
            parts = l.split("|")

            if parts[1]:
                prop = CompletionProposal(parts[1])
                results.append(prop)
            else:
                variants = parts[3].split("<br/>")
                param_lists = [re.search(r'\((.*)\)', v).group(1)
                                    for v in variants]
                props = []
                for idx, pl in enumerate(param_lists):
                    params = [par.split(" ")[-1] for par in pl.split(", ")]
                    insert = ", ".join(["${%i:%s}" % (i, s)
                                        for i, s in
                                        zip(range(1, len(params) + 1), params)
                                        ])
                    props.append(CompletionProposal(variants[idx], insert))
                    with_snippets = True
                results.extend(props)

        return results, with_snippets


class JavaValidation(sublime_plugin.EventListener):
    '''Show Java errors as found by Eclipse on save and load.
    Will trigger Eclipse compiles.'''

    drawType = 4 | 32
    line_messages = {}

    def __init__(self, *args, **kwargs):
        sublime_plugin.EventListener.__init__(self, *args, **kwargs)
        self.lastCount = {}

    def on_load(self, view):
        if "Java.tmLanguage" in view.settings().get("syntax"):
            self.validate(view)

    def on_post_save(self, view):
        if "Java.tmLanguage" in view.settings().get("syntax"):
            self.validate(view)

            # sometimes, Eclipse will not report errors instantly
            # check again a bit later
            def validation_closure():
                self.validate(view)
            sublime.set_timeout(validation_closure, 1500)

    def validate(self, view):
        if not check_eclim(view):
            return
        line_messages = JavaValidation.line_messages
        project, file = eclim.get_context(view.file_name())
        out = eclim.update_java_src(project, file)
        problems = eclim.parse_problems(out)
        vid = view.id()
        line_messages[vid] = {}
        for e in problems['errors']:
            l_no = int(e['line'])
            if not line_messages[vid].get(l_no, None):
                line_messages[vid][l_no] = []
            line_messages[vid][l_no].append(e['message'])
        self.visualize(view)

    def visualize(self, view):
        view.erase_regions('subclim-errors')
        lines = JavaValidation.line_messages[view.id()].keys()

        outlines = [view.line(view.text_point(lineno - 1, 0))
                    for lineno in lines]
        view.add_regions(
            'subclim-errors', outlines, 'keyword', JavaValidation.drawType)

    def on_selection_modified(self, view):
        if "Java.tmLanguage" in view.settings().get("syntax"):
            line_messages = JavaValidation.line_messages
            vid = view.id()
            lineno = view.rowcol(view.sel()[0].end())[0] + 1
            if vid in line_messages and lineno in line_messages[vid]:
                view.set_status(
                    'subclim', '; '.join(line_messages[vid][lineno]))
            else:
                view.erase_status('subclim')


class JavaImportClassUnderCursor(sublime_plugin.TextCommand):
    '''Will try to find a suitable class for importing using
    Eclipse's auto import features. Displays a menu if there are
    alternatives.'''

    def run(self, edit, block=False):
        if not check_eclim(self.view):
            return
        project, file = eclim.get_context(self.view.file_name())
        pos = self.view.sel()[0]
        word = self.view.substr(self.view.word(pos))
        class_names = self.call_eclim(project, word)
        if not class_names:
            display_error(self.view, "No suitable class found!")
            return
        if len(class_names) == 1:
            self.add_import(class_names[0], edit)
        else:
            self.edit = edit
            self.possible_imports = class_names
            self.show_import_menu()

    def call_eclim(self, project, identifier):
        self.view.run_command("save")
        eclim.update_java_src(project, file)
        complete_cmd = "-command java_import \
                                -n %s \
                                -p %s" % (project, identifier)
        class_name = eclim.call_eclim(complete_cmd)
        if class_name:
            return class_name.strip().split("\n")
        else:
            return []

    def show_import_menu(self):
        self.view.window().show_quick_panel(
            self.possible_imports, self.import_selected,
            sublime.MONOSPACE_FONT)

    def import_selected(self, selected_idx):
        self.add_import(self.possible_imports[selected_idx], self.edit)

    def add_import(self, class_name, edit):
        import_string = "import " + class_name + ";\n"
        lines = self.view.lines(sublime.Region(0, self.view.size()))
        last_import_region = sublime.Region(-1, -1)
        package_definition = sublime.Region(-1, -1)
        for l in lines:
            l_str = self.view.substr(l)
            if "{" in l_str:
                break
            if "package" in l_str:
                package_definition = l
            if "import" in l_str:
                last_import_region = l

        if last_import_region == sublime.Region(-1, -1):
            last_import_region = package_definition
            import_string = "\n" + import_string
        self.view.insert(edit, last_import_region.b + 1, import_string)