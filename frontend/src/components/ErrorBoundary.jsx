import React from 'react'

const COPY = {
  ru: { title: 'Не удалось открыть рабочее пространство', body: 'Обновите страницу. Если ошибка повторится, сообщите администратору идентификатор запроса из журнала сервера.', action: 'Обновить' },
  kk: { title: 'Жұмыс кеңістігін ашу мүмкін болмады', body: 'Бетті жаңартыңыз. Қате қайталанса, сервер журналындағы сұрау идентификаторын әкімшіге жіберіңіз.', action: 'Жаңарту' },
  en: { title: 'The workspace could not be opened', body: 'Reload the page. If it happens again, send the server request ID to the administrator.', action: 'Reload' },
}

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('Uncaught workspace error', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    const locale = (navigator.language || 'en').slice(0, 2)
    const copy = COPY[locale] || COPY.en
    return (
      <main className="fatal-error" role="alert">
        <div className="fatal-error-card">
          <span className="fatal-error-mark" aria-hidden="true">!</span>
          <h1>{copy.title}</h1>
          <p>{copy.body}</p>
          <button type="button" onClick={() => window.location.reload()}>{copy.action}</button>
        </div>
      </main>
    )
  }
}
