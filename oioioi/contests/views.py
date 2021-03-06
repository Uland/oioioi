from operator import itemgetter
import sys
import zipfile
import mimetypes

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.urlresolvers import reverse
from django.db.models import Q
from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.utils import translation
from django.utils.translation import ungettext_lazy, ugettext_lazy as _
from django.views.decorators.http import require_POST
from django.http import Http404
from django.utils.safestring import mark_safe
from django.core.exceptions import SuspiciousOperation

from oioioi.base.menu import menu_registry
from oioioi.base.permissions import not_anonymous, enforce_condition
from oioioi.base.utils.redirect import safe_redirect
from oioioi.base.utils.user_selection import get_user_hints_view
from oioioi.base.main_page import register_main_page_view
from oioioi.contests.controllers import submission_template_context
from oioioi.contests.forms import SubmissionForm, GetUserInfoForm
from oioioi.contests.models import Contest, ProblemInstance, Submission, \
        SubmissionReport, ContestAttachment
from oioioi.contests.utils import visible_contests, can_enter_contest, \
        can_see_personal_data, is_contest_admin, has_any_submittable_problem, \
        visible_rounds, visible_problem_instances, contest_exists, \
        get_submission_or_error, is_contest_observer
from oioioi.filetracker.utils import stream_file
from oioioi.problems.models import ProblemStatement, ProblemAttachment
from oioioi.problems.utils import query_statement, query_zip


@register_main_page_view(order=900)
def main_page_view(request):
    if not Contest.objects.exists():
        return TemplateResponse(request, 'contests/index-no-contests.html')
    return redirect('select_contest')


def select_contest_view(request):
    contests = visible_contests(request)
    return TemplateResponse(request, 'contests/select_contest.html',
            {'contests': contests})


@enforce_condition(contest_exists & can_enter_contest)
def default_contest_view(request):
    url = request.contest.controller.default_view(request)
    return HttpResponseRedirect(url)


@menu_registry.register_decorator(_("Problems"), lambda request:
        reverse('problems_list'), order=100)
@enforce_condition(contest_exists & can_enter_contest)
def problems_list_view(request):
    controller = request.contest.controller
    problem_instances = visible_problem_instances(request)
    problems_statements = [(pi, controller.can_see_statement(request, pi))
                           for pi in problem_instances]
    show_rounds = len(frozenset(pi.round_id for pi in problem_instances)) > 1
    return TemplateResponse(request, 'contests/problems_list.html',
        {'problem_instances': problems_statements,
         'show_rounds': show_rounds,
         'problems_on_page': getattr(settings, 'PROBLEMS_ON_PAGE', 100)})


@enforce_condition(contest_exists & can_enter_contest)
def problem_statement_view(request, problem_instance):
    controller = request.contest.controller
    pi = get_object_or_404(ProblemInstance, round__contest=request.contest,
            short_name=problem_instance)

    if not controller.can_see_problem(request, pi) or \
            not controller.can_see_statement(request, pi):
        raise PermissionDenied

    statement = query_statement(pi.problem)

    if not statement:
        return TemplateResponse(request, 'contests/no_problem_statement.html',
                    {'problem_instance': pi})

    if statement.extension == '.zip':
        return redirect('problem_statement_zip_index',
                contest_id=request.contest.id,
                problem_instance=problem_instance, statement_id=statement.id)
    return stream_file(statement.content)


@enforce_condition(contest_exists & can_enter_contest)
def problem_statement_zip_index_view(request, problem_instance,
        statement_id):

    response = problem_statement_zip_view(request,
            problem_instance, statement_id, 'index.html')

    problem_statement = get_object_or_404(ProblemStatement, id=statement_id)

    return TemplateResponse(request, 'contests/html_statement.html',
            {'content': mark_safe(response.content),
             'problem_name': problem_statement.problem.name})


@enforce_condition(contest_exists & can_enter_contest)
def problem_statement_zip_view(request, problem_instance,
        statement_id, path):
    controller = request.contest.controller
    pi = get_object_or_404(ProblemInstance, round__contest=request.contest,
            short_name=problem_instance)
    statement = get_object_or_404(ProblemStatement,
        problem__probleminstance=pi, id=statement_id)

    if not controller.can_see_problem(request, pi) or \
            not controller.can_see_statement(request, pi):
        raise PermissionDenied

    return query_zip(statement, path)


@menu_registry.register_decorator(_("Submit"), lambda request:
        reverse('submit'), order=300)
@enforce_condition(contest_exists & can_enter_contest)
@enforce_condition(has_any_submittable_problem,
                   template='contests/nothing_to_submit.html')
def submit_view(request):
    if request.method == 'POST':
        form = SubmissionForm(request, request.POST, request.FILES)
        if form.is_valid():
            request.contest.controller.create_submission(request,
                    form.cleaned_data['problem_instance'], form.cleaned_data)
            return redirect('my_submissions', contest_id=request.contest.id)
    else:
        form = SubmissionForm(request)
    return TemplateResponse(request, 'contests/submit.html', {'form': form})


@menu_registry.register_decorator(_("My submissions"), lambda request:
        reverse('my_submissions'), order=400)
@enforce_condition(not_anonymous & contest_exists & can_enter_contest)
def my_submissions_view(request):
    queryset = Submission.objects \
            .filter(problem_instance__contest=request.contest) \
            .order_by('-date') \
            .select_related('user', 'problem_instance',
                            'problem_instance__contest',
                            'problem_instance__round',
                            'problem_instance__problem')
    controller = request.contest.controller
    queryset = controller.filter_my_visible_submissions(request, queryset)
    header = controller.render_my_submissions_header(request, queryset.all())
    submissions = [submission_template_context(request, s) for s in queryset]
    show_scores = any(s['can_see_score'] for s in submissions)
    return TemplateResponse(request, 'contests/my_submissions.html',
        {'header': header,
         'submissions': submissions, 'show_scores': show_scores,
         'submissions_on_page': getattr(settings, 'SUBMISSIONS_ON_PAGE', 100)})


def submission_view_unsafe(request, submission):
    controller = submission.problem_instance.controller
    header = controller.render_submission(request, submission)
    footer = controller.render_submission_footer(request, submission)
    reports = []
    queryset = SubmissionReport.objects.filter(submission=submission). \
        prefetch_related('scorereport_set')
    for report in controller.filter_visible_reports(request, submission,
            queryset.filter(status='ACTIVE')):
        reports.append(controller.render_report(request, report))

    all_reports = is_contest_admin(request) and \
        controller.filter_visible_reports(request, submission, queryset) or \
        []

    return TemplateResponse(request, 'contests/submission.html',
                {'submission': submission, 'header': header, 'footer': footer,
                    'reports': reports, 'all_reports': all_reports})


@enforce_condition(contest_exists & can_enter_contest)
def submission_view(request, submission_id):
    return submission_view_unsafe(request,
                get_submission_or_error(request, submission_id))


@enforce_condition(contest_exists & is_contest_admin)
def report_view(request, submission_id, report_id):
    submission = get_submission_or_error(request, submission_id)
    controller = request.contest.controller
    queryset = SubmissionReport.objects.filter(submission=submission)
    report = get_object_or_404(queryset, id=report_id)
    return HttpResponse(controller.render_report(request, report))


@require_POST
def rejudge_submission_view_unsafe(request, submission):
    submission.problem_instance.controller \
        .judge(submission, request.GET.dict(), is_rejudge=True)
    messages.info(request, _("Rejudge request received."))


@enforce_condition((contest_exists & is_contest_admin))
@require_POST
def rejudge_submission_view(request, submission_id):
    submission = get_submission_or_error(request, submission_id)
    rejudge_submission_view_unsafe(request, submission)
    return redirect('submission', contest_id=request.contest.id,
            submission_id=submission_id)


@require_POST
def change_submission_kind_view_unsafe(request, submission, kind):
    controller = submission.problem_instance.controller
    if kind in controller.valid_kinds_for_submission(submission):
        controller.change_submission_kind(submission, kind)
        messages.success(request, _("Submission kind has been changed."))
    else:
        messages.error(request,
            _("%(kind)s is not valid kind for submission %(submission_id)d.")
            % {'kind': kind, 'submission_id': submission.id})


@enforce_condition(contest_exists & is_contest_admin)
@require_POST
def change_submission_kind_view(request, submission_id, kind):
    submission = get_submission_or_error(request, submission_id)
    change_submission_kind_view_unsafe(request, submission, kind)
    return redirect('submission', contest_id=request.contest.id,
                    submission_id=submission_id)


@menu_registry.register_decorator(_("Files"), lambda request:
        reverse('contest_files'), order=200)
@enforce_condition(not_anonymous & contest_exists & can_enter_contest)
def contest_files_view(request):
    contest_files = ContestAttachment.objects.filter(contest=request.contest) \
        .filter(Q(round__isnull=True) | Q(round__in=visible_rounds(request))) \
        .select_related('round')
    round_file_exists = contest_files.filter(round__isnull=False).exists()
    problem_instances = visible_problem_instances(request)
    problem_ids = [pi.problem_id for pi in problem_instances]
    problem_files = ProblemAttachment.objects \
            .filter(problem_id__in=problem_ids) \
            .select_related('problem')
    add_category_field = round_file_exists or problem_files.exists()
    rows = [{
        'category': cf.round if cf.round else '',
        'name': cf.filename,
        'description': cf.description,
        'link': reverse('contest_attachment',
            kwargs={'contest_id': request.contest.id, 'attachment_id': cf.id}),
        } for cf in contest_files]
    rows += [{
        'category': pf.problem,
        'name': pf.filename,
        'description': pf.description,
        'link': reverse('problem_attachment',
            kwargs={'contest_id': request.contest.id, 'attachment_id': pf.id}),
        } for pf in problem_files]
    rows.sort(key=itemgetter('name'))
    return TemplateResponse(request, 'contests/files.html', {'files': rows,
        'files_on_page': getattr(settings, 'FILES_ON_PAGE', 100),
        'add_category_field': add_category_field})


@enforce_condition(contest_exists & can_enter_contest)
def contest_attachment_view(request, attachment_id):
    attachment = get_object_or_404(ContestAttachment,
            contest_id=request.contest.id, id=attachment_id)
    if attachment.round and attachment.round not in visible_rounds(request):
        raise PermissionDenied
    return stream_file(attachment.content)


@enforce_condition(contest_exists & can_enter_contest)
def problem_attachment_view(request, attachment_id):
    attachment = get_object_or_404(ProblemAttachment, id=attachment_id)
    problem_instances = visible_problem_instances(request)
    problem_ids = [pi.problem_id for pi in problem_instances]
    if attachment.problem_id not in problem_ids:
        raise PermissionDenied
    return stream_file(attachment.content)


@enforce_condition(contest_exists & (is_contest_admin | is_contest_observer |
                                     can_see_personal_data))
def contest_user_hints_view(request):
    rcontroller = request.contest.controller.registration_controller()
    queryset = rcontroller.filter_participants(User.objects.all())
    return get_user_hints_view(request, 'substr', queryset)


@enforce_condition(contest_exists & (is_contest_admin | can_see_personal_data))
def user_info_view(request, user_id):
    controller = request.contest.controller
    rcontroller = controller.registration_controller()
    user = get_object_or_404(User, id=user_id)

    if not request.user.is_superuser and (not user in rcontroller
            .filter_users_with_accessible_personal_data(User.objects.all())
                    or user.is_superuser):
        raise PermissionDenied

    infolist = sorted(
            controller.get_contest_participant_info_list(request, user) +
            rcontroller.get_contest_participant_info_list(request, user),
            reverse=True)
    info = "".join(html for (_p, html) in infolist)
    return TemplateResponse(request, 'contests/user_info.html', {
            'target_user_name': controller.get_user_public_name(request, user),
            'info': info})


@enforce_condition(contest_exists & (is_contest_admin | can_see_personal_data))
@require_POST
def user_info_redirect_view(request):
    form = GetUserInfoForm(request, request.POST)
    if not form.is_valid():
        return TemplateResponse(request, 'simple-centered-form.html', {
                'form': form,
                'action': reverse('user_info_redirect',
                        kwargs={'contest_id': request.contest.id}),
                'title': _("See user info page")})

    user = form.cleaned_data['user']

    return safe_redirect(request, reverse('user_info', kwargs={
                        'contest_id': request.contest.id,
                        'user_id': user.id}))


@enforce_condition(contest_exists & is_contest_admin)
def rejudge_all_submissions_for_problem_view(request, problem_instance_id):
    problem_instance = get_object_or_404(ProblemInstance,
                                         id=problem_instance_id)
    count = problem_instance.submission_set.count()
    if request.POST:
        for submission in problem_instance.submission_set.all():
            problem_instance.controller.judge(submission, request.GET.dict(),
                                              is_rejudge=True)
        messages.info(request,
                      ungettext_lazy("%(count)d rejudge request received.",
                      "%(count)d rejudge requests reveived.",
                      count) % {'count': count})
        problem_instance.needs_rejudge = False
        problem_instance.save()
        return safe_redirect(request, reverse(
            'oioioiadmin:contests_probleminstance_changelist'))

    return TemplateResponse(request, "contests/confirm_rejudge.html",
                            {'count': count})
