define(['jquery'], function ($) {
    var CustomWidget = function () {
      var self = this;
  
      this.callbacks = {
        settings: function () {
          return true;
        },
        init: function () {
          return true;
        },
        bind_actions: function () {
          return true;
        },
        render: function () {
          return true;
        },
        destroy: function () {},
        onSave: function () {
          return true;
        }
      };
      return this;
    };
    return CustomWidget;
  });