import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers


User = get_user_model()
MAX_AVATAR_SIZE = 5 * 1024 * 1024


class RegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    class Meta:
        model = User
        fields = ["id", "username", "email", "password"]
        read_only_fields = ["id"]

    def validate_email(self, value):
        normalized_email = value.strip().lower()
        if User.objects.filter(email__iexact=normalized_email).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return normalized_email

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class AccountSerializer(serializers.ModelSerializer):
    avatar = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "avatar"]
        read_only_fields = fields

    def get_avatar(self, user):
        return user.avatar.url if user.avatar else None


class AvatarUploadSerializer(serializers.ModelSerializer):
    avatar = serializers.FileField(required=True)

    class Meta:
        model = User
        fields = ["avatar"]

    def validate_avatar(self, upload):
        if upload.size > MAX_AVATAR_SIZE:
            raise serializers.ValidationError("Avatar file must be 5 MB or smaller.")

        extension = os.path.splitext(upload.name)[1].lower()
        header = upload.read(12)
        upload.seek(0)
        is_png = extension == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n")
        is_jpeg = extension in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff")
        is_webp = extension == ".webp" and header.startswith(b"RIFF") and header[8:12] == b"WEBP"
        if not (is_png or is_jpeg or is_webp):
            raise serializers.ValidationError("Use a valid PNG, JPG, or WEBP image.")
        return upload