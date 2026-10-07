from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from . import notify
from .models import Invitation, Membership
from .serializers import InvitationSerializer, MembershipSerializer


def _owned_greenhouse_ids(user):
    return Membership.objects.filter(user=user, role=Membership.Role.OWNER).values_list("greenhouse_id", flat=True)


def _require_owner_of(user, greenhouse):
    if user.is_staff:
        return
    if not Membership.objects.filter(user=user, greenhouse=greenhouse, role=Membership.Role.OWNER).exists():
        raise PermissionDenied("Solo el propietario puede administrar los miembros.")


class MembershipViewSet(viewsets.ModelViewSet):
    """
    Quién tiene acceso a qué invernadero. Un usuario que no es Owner del
    invernadero en cuestión (ni staff) no puede ver ni modificar sus
    membresías.

    Para dar acceso a alguien, el Owner manda una INVITACIÓN
    (/api/v1/invitations/) que esa persona acepta o rechaza. Crear la
    membresía directo (POST aquí) queda solo para staff.

    `?greenhouse=<id>` filtra por invernadero.
    """
    serializer_class = MembershipSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Membership.objects.select_related("user", "greenhouse")
        if not user.is_staff:
            qs = qs.filter(greenhouse_id__in=_owned_greenhouse_ids(user))
        gh = self.request.query_params.get("greenhouse")
        if gh and gh.isdigit():
            qs = qs.filter(greenhouse_id=int(gh))
        return qs

    def perform_create(self, serializer):
        if not self.request.user.is_staff:
            raise PermissionDenied(
                "Para dar acceso a alguien, mándale una invitación (POST /api/v1/invitations/): "
                "tendrá acceso cuando la acepte."
            )
        serializer.save()

    def perform_update(self, serializer):
        instance = serializer.instance
        _require_owner_of(self.request.user, instance.greenhouse)
        # A quién pertenece y de qué invernadero es no se cambian: si se pudiera,
        # un owner movería su membresía de owner a un invernadero ajeno.
        for field in ("user", "greenhouse"):
            new = serializer.validated_data.get(field)
            if new is not None and new != getattr(instance, field):
                raise ValidationError({field: "No se puede cambiar. Quita esta membresía e invita de nuevo."})
        old_role = instance.role
        membership = serializer.save()
        if membership.role != old_role:
            notify.role_changed(membership, old_role, self.request.user, self.request)

    def perform_destroy(self, instance):
        _require_owner_of(self.request.user, instance.greenhouse)
        user, greenhouse = instance.user, instance.greenhouse
        instance.delete()
        notify.access_removed(user, greenhouse, self.request.user)


class InvitationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                        viewsets.GenericViewSet):
    """
    /api/v1/invitations/

    - GET: las invitaciones que me mandaron (`?box=received`, por defecto) o
      las que mandaron los propietarios de mis invernaderos (`?box=sent`,
      con `?greenhouse=<id>`). `?status=` filtra (por defecto `pending`;
      `all` = todas).
    - POST (Owner del invernadero o staff): `{greenhouse, invite, role}`.
      Le llega un correo a la persona invitada, si tiene email.
    - POST {id}/accept/ y {id}/decline/: solo la persona invitada.
    - POST {id}/cancel/: el Owner del invernadero (o staff), mientras siga pendiente.
    """
    serializer_class = InvitationSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Invitation.objects.select_related("user", "greenhouse", "invited_by")
        params = self.request.query_params
        if self.action == "list":
            if params.get("box") == "sent":
                if not user.is_staff:
                    qs = qs.filter(greenhouse_id__in=_owned_greenhouse_ids(user))
            else:
                qs = qs.filter(user=user)
            gh = params.get("greenhouse")
            if gh and gh.isdigit():
                qs = qs.filter(greenhouse_id=int(gh))
            st = params.get("status", Invitation.Status.PENDING)
            if st != "all":
                qs = qs.filter(status=st)
            return qs
        if user.is_staff:
            return qs
        return qs.filter(Q(user=user) | Q(greenhouse_id__in=_owned_greenhouse_ids(user)))

    def perform_create(self, serializer):
        _require_owner_of(self.request.user, serializer.validated_data["greenhouse"])
        try:
            with transaction.atomic():
                inv = serializer.save(invited_by=self.request.user)
        except IntegrityError:
            raise ValidationError({"invite": "Esa persona ya tiene una invitación pendiente a este invernadero."})
        notify.invitation_created(inv, self.request)

    def _pending(self, inv):
        if inv.status != Invitation.Status.PENDING:
            raise ValidationError({"detail": f"Esta invitación ya está {inv.get_status_display().lower()}."})

    def _answer(self, request, accept):
        inv = self.get_object()
        if inv.user_id != request.user.pk:
            raise PermissionDenied("Solo la persona invitada puede responder la invitación.")
        with transaction.atomic():
            inv = Invitation.objects.select_for_update().get(pk=inv.pk)
            self._pending(inv)
            if accept:
                Membership.objects.get_or_create(
                    user=inv.user, greenhouse=inv.greenhouse, defaults={"role": inv.role}
                )
            inv.status = Invitation.Status.ACCEPTED if accept else Invitation.Status.DECLINED
            inv.responded_at = timezone.now()
            inv.save(update_fields=["status", "responded_at"])
        notify.invitation_answered(inv, request)
        return Response(self.get_serializer(inv).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._answer(request, True)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        return self._answer(request, False)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        inv = self.get_object()
        _require_owner_of(request.user, inv.greenhouse)
        self._pending(inv)
        inv.status = Invitation.Status.CANCELLED
        inv.responded_at = timezone.now()
        inv.save(update_fields=["status", "responded_at"])
        return Response(self.get_serializer(inv).data, status=status.HTTP_200_OK)
