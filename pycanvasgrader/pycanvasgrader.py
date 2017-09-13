#!
"""
Automates the grading of programming assignments on Canvas.
MUST create an 'access.token' file in the same directory as this file with a valid Canvas OAuth2 token
REQUIRED File structure:
- pycanvasgrader
  -- skeletons
  -- temp
  access.token
  pycanvasgrader.py

  TODO test temp directory deletion timing on Windows
  TODO implement skeleton creation wizard
  TODO test/implement visual grading (ask for grade after test runs)
"""

# built-ins
import json
import os
import shutil
import re
import subprocess

# 3rd-party
import py
import requests

# globals
RUN_WITH_TESTS = False
ONLY_RUN_TESTS = False


class TestSkeleton:
    """
    An abstract skeleton to handle testing of a specific group of files
    """

    def __init__(self, descriptor: str, automation_level: str, tests: list):
        """
        :param descriptor: The description of this TestSkeleton
        :param tests: A list of AssignmentTest objects. These will be run in the order that they are added.
        :param automation_level: How many questions the grader should ask
        """
        self.descriptor = descriptor
        self.tests = tests
        self.automation_level = automation_level

    @classmethod
    def from_file(cls, filename):
        with open('skeletons/' + filename) as skeleton_file:
            data = json.load(skeleton_file)
            try:
                descriptor = data['descriptor']
                tests = data['tests']
            except KeyError:
                return None
            else:
                test_list = []
                for json_dict in tests:
                    test = AssignmentTest.from_json_dict(tests[json_dict])
                    if test is not None:
                        test_list.append(test)

                return TestSkeleton(descriptor, test_list)

    def run_tests(self) -> int:
        total_score = 0
        for count, test in enumerate(self.tests):
            print('\n--Running test %i--' % (count + 1))
            if test.run_and_match():
                if test.point_val > 0:
                    print('--Adding %i points--' % test.point_val)
                elif test.point_val == 0:
                    print('--No points set for this test--')
                else:
                    print('--Subtracting %i points--' % abs(test.point_val))
                total_score += test.point_val
            print('--Current score: %i--' % total_score)
        return total_score


# TODO Create tests
class AssignmentTest:
    """
    An abstract test to be run on an assignment submission
    TODO 'sequential' command requirement
    """

    def __init__(self, command: str, args: str = None, single_file: bool = False, target_file: str = None,
                 ask_for_target: bool = False, include_filetype: bool = True, print_output: bool = True,
                 output_match: str = None, output_regex: str = None, numeric_match: list = None,
                 negate_match: bool = False, exact_match: bool = False, timeout: int = None, point_val: int = 0):
        """
        :param command: The command to be run.
        :param args: The arguments to pass to the command. Use %s to denote a file name
        :param single_file: Whether to assume the assignment is a single file and use the first file found as %s
        :param target_file: The file to replace %s with
        :param ask_for_target: Whether to prompt for a file in the current directory. Overrides file_target
        :param include_filetype: Whether to include the filetype in the %s substitution
        :param print_output: Whether to visibly print the output
        :param output_match: An exact string that the output should match. If this and output_regex are None, then this Command always 'matches'
        :param output_regex: A regular expression that the string should match. Combines with output_match.
        If this and output_match are None, then this Command always 'matches'
        :param numeric_match: Enables numeric matching. This overrides string and regex matching
        :param negate_match: Whether to negate the result of checking output_match and output_regex
        :param exact_match: Whether the naive string match (output_match) should be an exact check or a substring check
        :param timeout: Time, in seconds, that this Command should run for before timing out
        :param point_val: Amount of points that a successful match is worth (Can be negative)
        """
        self.command = command
        self.args = args
        self.single_file = single_file
        self.target_file = target_file
        self.ask_for_target = ask_for_target
        self.include_filetype = include_filetype
        self.output_match = output_match
        if output_regex is not None:
            self.output_regex = re.compile(output_regex)
        else:
            self.output_regex = None
        self.numeric_match = numeric_match
        self.negate_match = negate_match
        self.exact_match = exact_match
        self.print_output = print_output
        self.timeout = timeout
        self.point_val = point_val

    @classmethod
    def from_json_dict(cls, json_dict: dict):
        try:
            command = json_dict['command']
        except KeyError:
            return None
        else:
            args = json_dict.get('args')
            single_file = json_dict.get('single_file')
            target_file = json_dict.get('target_file')
            ask_for_target = json_dict.get('ask_for_target')
            include_filetype = json_dict.get('include_filetype')
            print_output = json_dict.get('print_output')
            output_match = json_dict.get('output_match')
            output_regex = json_dict.get('output_regex')
            numeric_match = json_dict.get('numeric_match')
            negate_match = json_dict.get('negate_match')
            exact_match = json_dict.get('exact_match')
            timeout = json_dict.get('timeout')
            point_val = json_dict.get('point_val')

            vars_dict = {'command': command, 'args': args, 'single_file': single_file, 'target_file': target_file,
                         'ask_for_target': ask_for_target, 'include_filetype': include_filetype,
                         'print_output': print_output, 'output_match': output_match, 'output_regex': output_regex,
                         'numeric_match': numeric_match, 'negate_match': negate_match, 'exact_match': exact_match,
                         'timeout': timeout, 'point_val': point_val}
            args_dict = {}
            for var_name, val in vars_dict.items():
                if val is not None:
                    args_dict[var_name] = val
            return AssignmentTest(**args_dict)

    @classmethod
    def target_prompt(cls, command: str):
        sub = 0
        file_list = []
        if len(os.listdir(os.getcwd())) < 1:
            print('This directory is empty, unable to choose a file for "%s" command' % command)
            return None

        print('Select a file for the "%s" command:' % command)
        for count, file_name in enumerate(os.listdir(os.getcwd())):
            if os.path.isdir(file_name):
                sub += 1
                continue
            file_list.append(file_name)
            print('%i.\t%s' % (count - sub + 1, file_name))  # The minus 1 is to hide the 0-based numbering

        selection = choose_val(len(file_list)) - 1
        return file_list[selection]

    def run(self) -> dict:
        """
        Runs the Command
        :return: A dictionary containing the command's return code, stdout, and stderr
        """
        command = self.command
        args = self.args
        filename = self.target_file
        if self.ask_for_target:
            filename = AssignmentTest.target_prompt(self.command)
        if not self.include_filetype:
            filename = os.path.splitext(filename)[0]
        if filename is not None:
            command = self.command.replace('%s', filename)
            args = self.args.replace('%s', filename)
        elif '%s' in self.command + '|' + self.args:
            print('No filename given, but this command contains filename wildcards (%s). This command will probably fail')

        proc = subprocess.run([command, args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=self.timeout, encoding='UTF-8', shell=True)
        return {'returncode': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr}

    def run_and_match(self) -> bool:
        """
        Runs the command and matches the output to the output_match/regex. If neither are defined then this always returns true
        :return: Whether the output matched or not
        """
        result = self.run()
        if self.print_output:
            print('\t--OUTPUT--')
            print(result['stdout'])
            print('\t--END OUTPUT--')
        if not any((self.output_match, self.output_regex)):
            return True

        if self.numeric_match is not None:
            numeric_match = list(self.numeric_match)  # Clone the list
            extracted_nums = []
            for t in result['stdout'].split():
                try:
                    extracted_nums.append(float(t))
                except ValueError:
                    pass
            if len(extracted_nums) == 0:
                return False

            for num in numeric_match:
                for number in extracted_nums:
                    if isinstance(num, list):
                        if num[0] <= number <= num[1]:
                            numeric_match.remove(num)
                    elif isinstance(num, (int, float)):
                        if number == num:
                            numeric_match.remove(num)

            return len(numeric_match) == 0

        if self.output_regex:
            if self.output_regex.match(result['stdout']):
                print('--Matched regular expression--')
                if self.negate_match:
                    return False
                return True

        if self.output_match:
            if self.exact_match:
                condition = self.output_match == result['stdout']
            else:
                condition = self.output_match in result['stdout']

            if condition:
                print('--Matched string comparison--')
                if self.negate_match:
                    return False
                return True

        return self.negate_match


class PyCanvasGrader:
    """
    A PyCanvasGrader object; responsible for communicating with the Canvas API
    """

    def __init__(self):
        self.token = self.authenticate()
        if self.token == 'none':
            print('Unable to retrieve OAuth2 token')
            exit()

        self.session = requests.Session()
        self.session.headers.update({'Authorization': 'Bearer ' + self.token})

    @staticmethod
    def authenticate() -> str:
        """
        Responsible for retrieving the OAuth2 token for the session.
        :return: The OAuth2 token

        TODO Talk about "proper" OAuth2 authentication
        """

        with open('access.token', 'r', encoding='UTF-8') as access_file:
            for line in access_file:
                token = line.strip()
                if isinstance(token, str) and len(token) > 2:
                    return token
        return 'none'

    def close(self):
        self.session.close()

    def courses(self, enrollment_type: str = None) -> list:
        """
        :param enrollment_type: (Optional) teacher, student, ta, observer, designer
        :return: A list of the user's courses as dictionaries, optionally filtered by enrollment_type
        """
        url = 'https://sit.instructure.com/api/v1/courses'
        if enrollment_type is not None:
            url += '?enrollment_type=' + enrollment_type

        response = self.session.get(url)
        return json.loads(response.text)

    def assignments(self, course_id: int, ungraded: bool = True) -> list:
        """
        :param course_id: Course ID to filter by
        :param ungraded: Whether to filter assignments by only those that have ungraded work. Default: True
        :return: A list of the course's assignments
        """
        url = 'https://sit.instructure.com/api/v1/courses/' + str(course_id) + '/assignments'
        if ungraded:
            url += '?bucket=ungraded'

        response = self.session.get(url)
        return json.loads(response.text)

    def submissions(self, course_id: int, assignment_id: int) -> list:
        """
        :param course_id: The ID of the course containing the assignment
        :param assignment_id: The ID of the assignment
        :return: A list of the assignment's submissions
        """
        url = 'https://sit.instructure.com/api/v1/courses/' + str(course_id) + '/assignments/' + str(assignment_id) + '/submissions'

        response = self.session.get(url)
        return json.loads(response.text)

    def download_submission(self, submission: dict, filepath: str) -> bool:
        """
        Attempts to download the attachments for a given submission into the requested directory. Creates the directory if it does not exist.
        :param submission: The submission dictionary
        :param filepath: the path where the submission attachments will be placed
        :return: True if the request succeeded, False otherwise
        """
        try:
            user_id = submission['user_id']
            attachments = submission['attachments']
        except ValueError:
            return False

        for attachment in attachments:
            try:
                url = attachment['url']
                filename = attachment['filename']
            except ValueError:
                return False

            os.makedirs(os.path.join('temp', str(user_id)), exist_ok=True)
            r = self.session.get(url, stream=True)
            with open(os.path.join(filepath, filename), 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024):
                    if chunk:
                        f.write(chunk)
        return True

    def user(self, course_id: int, user_id: int) -> dict:
        """
        :param course_id: The class to search
        :param user_id: The ID of the user
        :return: A dictionary with the user's information
        """
        url = 'https://sit.instructure.com/api/v1/courses/%i/users/%i' % (course_id, user_id)

        response = self.session.get(url)
        return json.loads(response.text)

    def grade_submission(self, course_id, assignment_id, user_id, grade):
        url = 'https://sit.instructure.com/api/v1/courses/%i/assignments/%i/submissions/%i/?submission[posted_grade]=%i' \
              % (course_id, assignment_id, user_id, grade)

        response = self.session.put(url)
        return json.loads(response.text)


def choose_val(hi_num: int, allow_zero: bool = False) -> int:
    val = 'none'

    while True:
        if str.isdigit(val) and int(val) <= hi_num:
            if (allow_zero and int(val) >= 0) or (not allow_zero and int(val) > 0):
                break
        val = input()
    return int(val)


def choose_bool() -> bool:
    val = 'none'
    while not str.lower(val) in ['y', 'n', 'yes', 'no']:
        val = input()
    return val in ['y', 'yes']


def parse_skeletons() -> list:
    """
    Responsible for validating and parsing the skeleton files in the "skeletons" directory
    :return: A list of valid skeletons
    """
    skeleton_list = []
    for skeleton_file in os.listdir('skeletons'):
        skeleton = TestSkeleton.from_file(skeleton_file)
        if skeleton is not None:
            skeleton_list.append(skeleton)
    return skeleton_list


def restart_program(grader: PyCanvasGrader):
    grader.close()
    init_tempdir()
    main()
    exit(0)


def init_tempdir():
        try:
            if os.path.exists('temp'):
                os.rename('temp', 'old-temp')
                shutil.rmtree(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'old-temp'))
            os.makedirs('temp', exist_ok=True)
        except BaseException:
            print('An error occurred while initializing the "temp" directory. Please delete/create the directory manually and re-run the program')
            exit(1)


def main():
    init_tempdir()
    # Initialize grading session and fetch courses
    grader = PyCanvasGrader()

    action_list = ['teacher', 'ta']

    print('Choose a class role to filter by:')
    for count, action in enumerate(action_list, 1):
        print('%i.\t%s' % (count, action))
    action_choice = choose_val(len(action_list)) - 1
    selected_role = action_list[action_choice]

    course_list = grader.courses(selected_role)
    if len(course_list) < 1:
        input('No courses were found. Press enter to restart')
        restart_program(grader)

    # Have user select course
    print('Choose a course from the following list:')
    for count, course in enumerate(course_list, 1):
        print('%i.\t%s (%s)' % (count, course.get('name'), course.get('course_code')))
    course_choice = choose_val(len(course_list)) - 1  # the minus 1 is to hide the 0-based numbering

    print('Show only ungraded assignments? (y or n):')
    ungraded = choose_bool()
    course_id = course_list[course_choice].get('id')
    assignment_list = grader.assignments(course_list[course_choice].get('id'), ungraded=ungraded)

    if len(assignment_list) < 1:
        input('No assignments were found. Press enter to restart')
        restart_program(grader)

    # Have user choose assignment
    print('Choose an assignment to grade:')
    for count, assignment in enumerate(assignment_list, 1):
        print('%i.\t%s' % (count, assignment.get('name')))
    assignment_choice = choose_val(len(assignment_list)) - 1
    assignment_id = assignment_list[assignment_choice].get('id')

    # Get list of submissions for this assignment
    submission_list = grader.submissions(course_id, assignment_id)
    if len(submission_list) < 1:
        input('There are no submissions for this assignment. Press enter to restart')
        restart_program(grader)

    print('Only grade currently ungraded submissions? (y or n):')
    ungraded_only = choose_bool()
    # Match the user IDs found in the zip with the IDs in the online submission
    user_submission_dict = {}
    for submission in submission_list:
        if ungraded_only and submission.get('grader_id') is not None:  # Skip assignments that have been graded already
            continue
        user_id = submission.get('user_id')
        if submission.get('attachments') is not None:
            if grader.download_submission(submission, os.path.join('temp', str(user_id))):
                user_submission_dict[user_id] = submission['id']
            else:
                print('There was a problem downloading this user\'s submission. Skipping.')

    if len(user_submission_dict) < 1:
        input('Could not download any submissions. Press enter to restart')
        restart_program(grader)

    s = ''
    if len(user_submission_dict) > 1:
        s = 's'
    print('Successfully retrieved %i submission%s. Is this correct? (y or n):' % (len(user_submission_dict), s))
    correct = choose_bool()
    if not correct:
        restart_program(grader)

    skeleton_list = parse_skeletons()
    if len(skeleton_list) < 1:
        print('Could not find any skeleton files in the skeletons directory. Would you like to create one now? (y or n):')
        if choose_bool():
            print('unimplemented')
        else:
            pass
        exit(0)

    print('Choose a skeleton to use for grading this assignment:')
    for count, skeleton in enumerate(skeleton_list, 1):
        print('%i.\t%s' % (count, skeleton.descriptor))
    skeleton_choice = choose_val(len(skeleton_list)) - 1
    selected_skeleton = skeleton_list[skeleton_choice]

    name_dict = {}
    print('Students to grade: [Name (email)]\n----')
    for user_id in user_submission_dict:
        user_data = grader.user(course_id, user_id)
        if user_data.get('name') is not None:
            name_dict[user_id] = user_data['name']
        print(str(user_data.get('name')) + '\t(%s)' % user_data.get('email'))
    print('----\n')
    input('Press enter to begin grading\n')
    for cur_user_id in user_submission_dict:
        try:
            os.chdir(os.path.join('temp', str(cur_user_id)))
        except (WindowsError, OSError):
            print('Could not access files for user "%i". Skipping' % cur_user_id)
            continue
        print('--Grading user "%s"--' % name_dict.get(cur_user_id))
        score = selected_skeleton.run_tests()

        if score < 0:
            score = 0
        action_list = ['Submit this grade', 'Modify this grade', 'Skip this submission', 'Re-grade this submission']

        while True:
            print('\n--All tests completed--\nGrade for this assignment: %i' % score)
            print('Choose an action:')
            for count, action in enumerate(action_list, 1):
                print('%i.\t%s' % (count, action))
            action_choice = choose_val(len(action_list)) - 1
            selected_action = action_list[action_choice]

            if selected_action == 'Submit this grade':
                grader.grade_submission(course_id, assignment_id, cur_user_id, score)
                print('Grade submitted')
                break
            elif selected_action == 'Modify this grade':
                print('Enter a new grade for this submission:')
                score = choose_val(1000, allow_zero=True)
            elif selected_action == 'Skip this submission':
                break
            elif selected_action == 'Re-grade this submission':
                score = selected_skeleton.run_tests()

    print('done')


if __name__ == '__main__':
    if RUN_WITH_TESTS or ONLY_RUN_TESTS:
        py.test.cmdline.main()
    if not ONLY_RUN_TESTS:
        main()
