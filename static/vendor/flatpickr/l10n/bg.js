/* Bulgarian locale for Flatpickr 4.x; vendored locale data. */
(function (global, factory) {
  typeof exports === 'object' && typeof module !== 'undefined' ? factory(exports) :
  typeof define === 'function' && define.amd ? define(['exports'], factory) :
  factory((global.bg = {}));
}(this, function (exports) {
  'use strict';
  var Bulgarian = {
    weekdays: { shorthand: ['Нд', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'], longhand: ['Неделя', 'Понеделник', 'Вторник', 'Сряда', 'Четвъртък', 'Петък', 'Събота'] },
    months: { shorthand: ['Яну', 'Фев', 'Мар', 'Апр', 'Май', 'Юни', 'Юли', 'Авг', 'Сеп', 'Окт', 'Ное', 'Дек'], longhand: ['Януари', 'Февруари', 'Март', 'Април', 'Май', 'Юни', 'Юли', 'Август', 'Септември', 'Октомври', 'Ноември', 'Декември'] },
    firstDayOfWeek: 1, rangeSeparator: ' до ', weekAbbreviation: 'Седм.', scrollTitle: 'Превъртете за промяна', toggleTitle: 'Кликнете за превключване', time_24hr: true
  };
  if (typeof window !== 'undefined' && window.flatpickr) window.flatpickr.l10ns.bg = Bulgarian;
  exports.Bulgarian = Bulgarian;
  exports.default = Bulgarian;
}));
