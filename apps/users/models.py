from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """
    Usuario del sistema.

    Por ahora no agrega campos nuevos: hereda username, email,
    password, first_name, last_name, is_staff, is_active, etc.
    de AbstractUser.

    Existe como modelo propio desde el día uno porque Django NO
    permite cambiar el modelo de usuario después de la primera
    migración sin reconstruir la base de datos. Migrar de
    auth.User a un modelo propio, a mitad de proyecto, es doloroso
    y evitable con este único archivo.

    Los roles y permisos por invernadero (Etapa 12) se agregarán
    aquí como campos nuevos o como un modelo relacionado
    (Membership), sin tocar nada de lo que ya funcione.
    """
    pass