from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual

router = APIRouter(prefix="/empresas", tags=["empresas"])


@router.get("/mi-empresa")
def ver_mi_empresa(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    if not usuario.empresa_id:
        raise HTTPException(status_code=404, detail="No perteneces a ninguna inmobiliaria")

    empresa = (
        db.query(models.Empresa)
        .options(joinedload(models.Empresa.agentes))
        .filter_by(id=usuario.empresa_id)
        .first()
    )
    return {
        "empresa": schemas.EmpresaOut.model_validate(empresa),
        "agentes": [
            {"id": a.id, "nombre": a.nombre, "email": a.email, "rol": a.rol_empresa.value if a.rol_empresa else None}
            for a in empresa.agentes
        ],
    }


@router.patch("/mi-empresa", response_model=schemas.EmpresaOut)
def actualizar_mi_empresa(
    payload: schemas.EmpresaActualizar,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    """Logo y breve presentación de la inmobiliaria — cualquier agente
    puede actualizarlo (no hace falta ser el propietario para esto, a
    diferencia de invitar/quitar agentes)."""
    if not usuario.empresa_id:
        raise HTTPException(status_code=404, detail="No perteneces a ninguna inmobiliaria")

    empresa = db.query(models.Empresa).filter_by(id=usuario.empresa_id).first()
    datos = payload.model_dump(exclude_unset=True)
    for campo, valor in datos.items():
        setattr(empresa, campo, valor)
    db.commit()
    db.refresh(empresa)
    return empresa


@router.post("/invitar", status_code=201)
def invitar_agente(
    payload: schemas.InvitarAgenteIn,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    """
    Añade a un usuario ya registrado como agente de tu inmobiliaria. Por
    simplicidad, la persona invitada debe haberse creado ya una cuenta
    normal en Hausbix de antemano (con ese email) — esto la asocia a tu
    empresa, no crea una cuenta nueva desde cero.
    """
    if usuario.rol_empresa != models.RolEmpresa.propietario:
        raise HTTPException(status_code=403, detail="Solo el propietario de la inmobiliaria puede invitar agentes")

    invitado = db.query(models.Usuario).filter(models.Usuario.email == payload.email).first()
    if not invitado:
        raise HTTPException(
            status_code=404,
            detail="No hay ninguna cuenta de Hausbix con ese email todavía — pide a esa persona que se registre primero",
        )
    if invitado.id == usuario.id:
        raise HTTPException(status_code=400, detail="No puedes invitarte a ti mismo")
    if invitado.empresa_id:
        raise HTTPException(status_code=400, detail="Esa persona ya pertenece a otra inmobiliaria")

    invitado.empresa_id = usuario.empresa_id
    invitado.rol_empresa = models.RolEmpresa.agente
    invitado.tipo_cuenta = models.TipoCuenta.inmobiliaria
    db.commit()

    return {"ok": True, "mensaje": f"{invitado.nombre} añadido como agente"}


@router.delete("/agentes/{agente_id}")
def quitar_agente(
    agente_id: str,
    traspasar_anuncios: bool = False,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    if usuario.rol_empresa != models.RolEmpresa.propietario:
        raise HTTPException(status_code=403, detail="Solo el propietario de la inmobiliaria puede quitar agentes")

    agente = db.query(models.Usuario).filter_by(id=agente_id, empresa_id=usuario.empresa_id).first()
    if not agente:
        raise HTTPException(status_code=404, detail="Ese agente no pertenece a tu inmobiliaria")
    if agente.id == usuario.id:
        raise HTTPException(status_code=400, detail="No puedes quitarte a ti mismo como propietario")

    if traspasar_anuncios:
        # Sus anuncios pasan al propietario, que sigue gestionándolos.
        db.query(models.Inmueble).filter_by(usuario_id=agente.id).update({"usuario_id": usuario.id})
    agente.empresa_id = None
    agente.rol_empresa = None
    agente.tipo_cuenta = models.TipoCuenta.particular
    db.commit()

    return {"ok": True}


def _solo_propietario(usuario: models.Usuario):
    if not usuario.empresa_id or usuario.rol_empresa != models.RolEmpresa.propietario:
        raise HTTPException(status_code=403, detail="Solo el propietario de la inmobiliaria puede hacer esto")


@router.get("/panel")
def panel_empresa(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    """
    Panel del propietario: resumen de toda la inmobiliaria, cifras por
    agente y la lista de TODOS los anuncios del equipo (con quién los
    lleva), para poder gestionarlos desde un solo sitio.
    """
    _solo_propietario(usuario)
    miembros = db.query(models.Usuario).filter_by(empresa_id=usuario.empresa_id).all()
    por_id = {m.id: m for m in miembros}
    inmuebles = (
        db.query(models.Inmueble)
        .options(joinedload(models.Inmueble.fotos))
        .filter(models.Inmueble.usuario_id.in_(list(por_id.keys())))
        .order_by(models.Inmueble.fecha_creacion.desc())
        .all()
    )

    agentes = {
        m.id: {
            "id": m.id, "nombre": m.nombre, "email": m.email,
            "rol": m.rol_empresa.value if m.rol_empresa else None,
            "anuncios": 0, "activos": 0, "contactos": 0,
        }
        for m in miembros
    }
    anuncios = []
    for i in inmuebles:
        a = agentes[i.usuario_id]
        a["anuncios"] += 1
        a["activos"] += 1 if i.activo else 0
        a["contactos"] += i.contactos_recibidos or 0
        fotos = sorted(i.fotos, key=lambda f: f.orden or 0)
        anuncios.append({
            "id": i.id, "titulo": i.titulo, "direccion": i.direccion,
            "activo": bool(i.activo),
            "estado_moderacion": i.estado_moderacion.value if i.estado_moderacion else "pendiente",
            "contactos": i.contactos_recibidos or 0,
            "foto": fotos[0].url if fotos else None,
            "agente_id": i.usuario_id, "agente_nombre": por_id[i.usuario_id].nombre,
        })

    return {
        "resumen": {
            "anuncios": len(anuncios),
            "activos": sum(1 for x in anuncios if x["activo"]),
            "pendientes_revision": sum(1 for x in anuncios if x["estado_moderacion"] == "pendiente"),
            "contactos": sum(x["contactos"] for x in anuncios),
            "agentes": len(miembros),
        },
        "agentes": list(agentes.values()),
        "anuncios": anuncios,
    }


def _anuncio_de_la_empresa(db: Session, usuario: models.Usuario, anuncio_id: str) -> models.Inmueble:
    inmueble = db.query(models.Inmueble).filter_by(id=anuncio_id).first()
    dueno = db.query(models.Usuario).filter_by(id=inmueble.usuario_id).first() if inmueble else None
    if not inmueble or not dueno or dueno.empresa_id != usuario.empresa_id:
        raise HTTPException(status_code=404, detail="Ese anuncio no pertenece a tu inmobiliaria")
    return inmueble


@router.patch("/anuncios/{anuncio_id}/activo")
def activar_anuncio_equipo(
    anuncio_id: str,
    activo: bool,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    """Pausar o reactivar un anuncio de cualquier agente del equipo."""
    _solo_propietario(usuario)
    inmueble = _anuncio_de_la_empresa(db, usuario, anuncio_id)
    inmueble.activo = activo
    db.commit()
    return {"ok": True, "activo": inmueble.activo}


@router.patch("/anuncios/{anuncio_id}/agente")
def reasignar_anuncio(
    anuncio_id: str,
    agente_id: str,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    """Pasa un anuncio a otro agente de la inmobiliaria (o al propio
    propietario) — p. ej. cuando un agente se va o cambia de zona."""
    _solo_propietario(usuario)
    inmueble = _anuncio_de_la_empresa(db, usuario, anuncio_id)
    destino = db.query(models.Usuario).filter_by(id=agente_id, empresa_id=usuario.empresa_id).first()
    if not destino:
        raise HTTPException(status_code=404, detail="Ese agente no pertenece a tu inmobiliaria")
    inmueble.usuario_id = destino.id
    db.commit()
    return {"ok": True, "agente_id": destino.id, "agente_nombre": destino.nombre}
