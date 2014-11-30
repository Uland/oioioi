#Workaround for https://code.djangoproject.com/ticket/23934
#Needs to be removed after Django 1.7.2 is released

import django
if django.VERSION >= (1, 7, 2):
    raise Exception("Django 1.7.2 was released. Remove the 'monkeypatch' app.")

from django.contrib.admin.options import csrf_protect_m, ModelAdmin, \
    IS_POPUP_VAR, TO_FIELD_VAR, helpers, transaction, _, force_text, \
    all_valid, unquote, PermissionDenied, DisallowedModelAdminToField, \
    Http404, escape, reverse


@csrf_protect_m
@transaction.atomic
def changeform_view(self, request, object_id=None, form_url='', extra_context=None):

    to_field = request.POST.get(TO_FIELD_VAR, request.GET.get(TO_FIELD_VAR))
    if to_field and not self.to_field_allowed(request, to_field):
        raise DisallowedModelAdminToField("The field %s cannot be referenced." % to_field)

    model = self.model
    opts = model._meta
    add = object_id is None

    if add:
        if not self.has_add_permission(request):
            raise PermissionDenied
        obj = None

    else:
        obj = self.get_object(request, unquote(object_id))

        if not self.has_change_permission(request, obj):
            raise PermissionDenied

        if obj is None:
            raise Http404(_('%(name)s object with primary key %(key)r does not exist.') % {
                'name': force_text(opts.verbose_name), 'key': escape(object_id)})

        if request.method == 'POST' and "_saveasnew" in request.POST:
            return self.add_view(request, form_url=reverse('admin:%s_%s_add' % (
                opts.app_label, opts.model_name),
                current_app=self.admin_site.name))

    ModelForm = self.get_form(request, obj)
    if request.method == 'POST':
        form = ModelForm(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            form_validated = True
            new_object = self.save_form(request, form, change=not add)
        else:
            form_validated = False
            new_object = form.instance
        formsets, inline_instances = self._create_formsets(request, new_object, change=not add)
        if all_valid(formsets) and form_validated:
            self.save_model(request, new_object, form, not add)
            self.save_related(request, form, formsets, not add)
            if add:
                self.log_addition(request, new_object)
                return self.response_add(request, new_object)
            else:
                change_message = self.construct_change_message(request, form, formsets)
                self.log_change(request, new_object, change_message)
                return self.response_change(request, new_object)
    else:
        if add:
            initial = self.get_changeform_initial_data(request)
            form = ModelForm(initial=initial)
            formsets, inline_instances = self._create_formsets(request, self.model(), change=False)
        else:
            form = ModelForm(instance=obj)
            formsets, inline_instances = self._create_formsets(request, obj, change=True)

    adminForm = helpers.AdminForm(
        form,
        list(self.get_fieldsets(request, obj)),
        self.get_prepopulated_fields(request, obj),
        self.get_readonly_fields(request, obj),
        model_admin=self)
    media = self.media + adminForm.media

    inline_formsets = self.get_inline_formsets(request, formsets, inline_instances, obj)
    for inline_formset in inline_formsets:
        media = media + inline_formset.media

    context = dict(self.admin_site.each_context(),
        title=(_('Add %s') if add else _('Change %s')) % force_text(opts.verbose_name),
        adminform=adminForm,
        object_id=object_id,
        original=obj,
        is_popup=(IS_POPUP_VAR in request.POST or
                  IS_POPUP_VAR in request.GET),
        to_field=to_field,
        media=media,
        inline_admin_formsets=inline_formsets,
        errors=helpers.AdminErrorList(form, formsets),
        preserved_filters=self.get_preserved_filters(request),
    )

    context.update(extra_context or {})

    return self.render_change_form(request, context, add=add, change=not add, obj=obj, form_url=form_url)


def _create_formsets(self, request, obj, change):
    "Helper function to generate formsets for add/change_view."
    formsets = []
    inline_instances = []
    prefixes = {}
    get_formsets_args = [request]
    if change:
        get_formsets_args.append(obj)
    for FormSet, inline in self.get_formsets_with_inlines(*get_formsets_args):
        prefix = FormSet.get_default_prefix()
        prefixes[prefix] = prefixes.get(prefix, 0) + 1
        if prefixes[prefix] != 1 or not prefix:
            prefix = "%s-%s" % (prefix, prefixes[prefix])
        formset_params = {
            'instance': obj,
            'prefix': prefix,
            'queryset': inline.get_queryset(request),
        }
        if request.method == 'POST':
            formset_params.update({
                'data': request.POST,
                'files': request.FILES,
                'save_as_new': '_saveasnew' in request.POST
            })
        formsets.append(FormSet(**formset_params))
        inline_instances.append(inline)
    return formsets, inline_instances


ModelAdmin.changeform_view = changeform_view
ModelAdmin._create_formsets = _create_formsets