from oioioi.programs.controllers import ProgrammingContestController
from oioioi.contests.models import Submission, SubmissionReport
from django.utils.translation import ugettext_lazy as _
from oioioi.contests.scores import IntegerScore
from oioioi.phase.models import Phase

class _FakePhase:
    multiplier = 100

class PhaseContestController(ProgrammingContestController):
    description = _("Phase contest")

    def fill_evaluation_environ(self, environ, submission):
        super(PhaseContestController, self) \
                .fill_evaluation_environ(environ, submission)

        environ['group_scorer'] = 'oioioi.programs.utils.min_group_scorer'
        environ['test_scorer'] = \
                'oioioi.programs.utils.threshold_linear_test_scorer'

    def update_user_result_for_problem(self, result):
        try:
            submissions = Submission.objects \
                .filter(problem_instance=result.problem_instance) \
                .filter(user=result.user) \
                .filter(score__isnull=False) \
                .exclude(status='CE') \
                .filter(kind='NORMAL') \
                .order_by('date')

            round = result.problem_instance.round

            phases = Phase.objects \
                .filter(round=round) \
                .order_by('start_date')

            cur_phase = _FakePhase()
            next_phase_index = 0;

            lastHighest = 0
            total = 0
            for s in submissions:
                if (next_phase_index < len(phases) and
                        phases[next_phase_index].start_date < s.date):
                    cur_phase = phases[next_phase_index]
                score = s.score.to_int()
                if score > lastHighest:
                    total += (score - lastHighest) * cur_phase.multiplier
                    lastHighest = score

            chosen_submission = submissions.latest() # mostly to link it later

            try:
                report = SubmissionReport.objects.get(
                        submission=chosen_submission, status='ACTIVE',
                        kind='NORMAL')
            except SubmissionReport.DoesNotExist:
                report = None

            result.score = IntegerScore(total / 100)
            result.status = chosen_submission.status
            result.submission_report = report
        except Submission.DoesNotExist:
            result.score = None
            result.status = None
            result.submission_report = None
