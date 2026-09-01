import React from 'react';

export default function PendingBootBanner({ pending, onClear, onReboot }) {
  if (!pending) return null;

  const exhausted = pending.attempts >= pending.max_attempts;

  return (
    <div className={`banner ${exhausted ? 'banner-error' : 'banner-warning'}`}>
      <span>
        {exhausted ? (
          <>
            <strong>{pending.image_name}</strong> falhou ao iniciar {pending.attempts} vezes e foi
            desativado automaticamente. O Manager continua sendo iniciado normalmente.
          </>
        ) : (
          <>
            <strong>{pending.image_name}</strong> está agendado para o próximo boot
            {pending.attempts > 0 && ` (tentativa ${pending.attempts} de ${pending.max_attempts})`}.
          </>
        )}
      </span>
      <span style={{ display: 'flex', gap: 8 }}>
        {!exhausted && (
          <button className="btn btn-sm btn-primary" onClick={onReboot}>
            Reiniciar agora
          </button>
        )}
        <button className="btn btn-sm" onClick={onClear}>
          Cancelar
        </button>
      </span>
    </div>
  );
}
