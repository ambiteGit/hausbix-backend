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

    agente.empresa_id = None
    agente.rol_empresa = None
    agente.tipo_cuenta = models.TipoCuenta.particular
    db.commit()

    return {"ok": True}
