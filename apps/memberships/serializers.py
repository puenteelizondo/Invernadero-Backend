from rest_framework import serializers

from .models import Membership


class MembershipSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "username", "greenhouse", "role", "created_at"]
        read_only_fields = ["created_at"]