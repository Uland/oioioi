import urllib

from django import forms
from django.shortcuts import redirect
from django.core.urlresolvers import reverse
from django.core.exceptions import PermissionDenied, ObjectDoesNotExist
from django.template import RequestContext
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.http import HttpResponseRedirect
from django.utils.translation import ugettext_lazy as _

from oioioi.base.utils import request_cached, get_user_display_name
from oioioi.base.utils.redirect import safe_redirect
from oioioi.contests.controllers import RegistrationController, \
        ContestController
from oioioi.contests.utils import is_contest_admin, can_see_personal_data
from oioioi.participants.models import Participant, RegistrationModel


class ParticipantsController(RegistrationController):
    registration_template = 'participants/registration.html'

    @property
    def form_class(self):
        return None

    @property
    def participant_admin(self):
        from oioioi.participants.admin import ParticipantAdmin
        return ParticipantAdmin

    def anonymous_can_enter_contest(self):
        return False

    def allow_login_as_public_name(self):
        """Determines if participants may choose to stay anonymous,
           i.e. use their logins as public names.
        """
        return False

    def filter_participants(self, queryset):
        return queryset.filter(participant__contest=self.contest,
                participant__status='ACTIVE')

    def filter_users_with_accessible_personal_data(self, queryset):
        return self.filter_participants(queryset)

    def can_register(self, request):
        return False

    def can_edit_registration(self, request, participant):
        if self.form_class is None:
            return False
        if is_contest_admin(request):
            return True
        if participant.status == 'BANNED':
            return False
        return bool(request.user == participant.user)

    def can_unregister(self, request, participant):
        return self.can_edit_registration(request, participant)

    def no_entry_view(self, request):
        if self.can_register(request):
            url = reverse('participants_register',
                        kwargs={'contest_id': self.contest.id}) + '?' + \
                    urllib.urlencode({'next': request.build_absolute_uri()})
            return HttpResponseRedirect(url)
        return super(ParticipantsController, self).no_entry_view(request)

    def get_model_class(self):
        """Returns registration model class used within current registration
           controller.

           The default implementation infers it from form_class form metadata.
           If there is no form_class, the default implementation returns
           ``None``.
        """
        if self.form_class is None:
            return None
        assert issubclass(self.form_class, forms.ModelForm), \
            'ParticipantsController.form_class must be a ModelForm'
        model_class = self.form_class._meta.model
        assert issubclass(model_class, RegistrationModel), \
            'ParticipantsController.form_class\'s model must be a ' \
            'subclass of RegistrationModel'
        return model_class

    def get_form(self, request, participant=None):
        if self.form_class is None:
            return None
        instance = None
        if participant:
            try:
                instance = participant.registration_model
            except ObjectDoesNotExist:
                pass
        # pylint: disable=not-callable
        if request.method == 'POST':
            form = self.form_class(request.POST, instance=instance)
        else:
            form = self.form_class(instance=instance)

        if self.allow_login_as_public_name():
            initial = participant.anonymous if participant else False
            form.fields['anonymous'] = forms.BooleanField(required=False,
                    label=_("Anonymous"), initial=initial,
                    help_text=_("Anonymous participant uses the account name "
                        "instead of the real name in rankings."))
        return form

    def handle_validated_form(self, request, form, participant):
        instance = form.save(commit=False)
        instance.participant = participant
        instance.save()
        participant.anonymous = form.cleaned_data.get('anonymous', False)
        participant.save()

    def _get_participant_for_form(self, request):
        try:
            participant = Participant.objects.get(contest=self.contest,
                    user=request.user)
            if not self.can_edit_registration(request, participant):
                raise PermissionDenied
        except Participant.DoesNotExist:
            participant = None
        if participant is None and not self.can_register(request):
            raise PermissionDenied
        return participant

    def registration_view(self, request):
        participant = self._get_participant_for_form(request)

        form = self.get_form(request, participant)
        assert form is not None, "can_register or can_edit_registration " \
            "returned True, but controller returns no registration form"

        if request.method == 'POST':
            if form.is_valid():
                participant, created = Participant.objects.get_or_create(
                        contest=self.contest, user=request.user)
                self.handle_validated_form(request, form, participant)
                if 'next' in request.GET:
                    return safe_redirect(request, request.GET['next'])
                else:
                    return redirect('default_contest_view',
                            contest_id=self.contest.id)
        can_unregister = False
        if participant:
            can_unregister = self.can_unregister(request, participant)
        context = {
            'form': form,
            'participant': participant,
            'can_unregister': can_unregister,
        }
        return TemplateResponse(request, self.registration_template, context)


@request_cached
def anonymous_participants(request):
    if not hasattr(request, 'contest'):
        return frozenset({})
    return frozenset((p.user for p in Participant.objects
            .filter(contest=request.contest, anonymous=True)
            .select_related('user')))


class AnonymousContestControllerMixin(object):
    def get_user_public_name(self, request, user):
        assert self.contest == request.contest
        if request.user.is_superuser or can_see_personal_data(request) \
                or not user in anonymous_participants(request):
            return get_user_display_name(user)
        else:
            return user.username

    def get_contest_participant_info_list(self, request, user):
        prev = super(AnonymousContestControllerMixin, self).\
                get_contest_participant_info_list(request, user)
        try:
            part = Participant.objects.get(user=user,
                                          contest=request.contest)
            context = {'participant': part}
            rendered_info = render_to_string(
                    'participants/participant_info.html',
                    context_instance=RequestContext(request, context))
            prev.append((98, rendered_info))
        except Participant.DoesNotExist:
            pass
        return prev

ContestController.mix_in(AnonymousContestControllerMixin)
