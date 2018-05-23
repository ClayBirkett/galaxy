define("mvc/user/user-quotameter",["exports","backbone","underscore","mvc/base-mvc","utils/localization"],function(t,e,r,o,a){"use strict";function i(t){return t&&t.__esModule?t:{default:t}}function s(t){if(t&&t.__esModule)return t;var e={};if(null!=t)for(var r in t)Object.prototype.hasOwnProperty.call(t,r)&&(e[r]=t[r]);return e.default=t,e}Object.defineProperty(t,"__esModule",{value:!0});var n=s(e),u=s(r),l=i(o),d=i(a),c=n.View.extend(l.default.LoggableMixin).extend({_logNamespace:"user",options:{warnAtPercent:85,errorAtPercent:100},initialize:function(t){this.log(this+".initialize:",t),u.extend(this.options,t),this.listenTo(this.model,"change:quota_percent change:total_disk_usage",this.render)},update:function(t){return this.log(this+" updating user data...",t),this.model.loadFromApi(this.model.get("id"),t),this},isOverQuota:function(){return null!==this.model.get("quota_percent")&&this.model.get("quota_percent")>=this.options.errorAtPercent},_render_quota:function(){var t=this.model.toJSON(),e=t.quota_percent,r=$(this._templateQuotaMeter(t)),o=r.find(".progress-bar");return this.isOverQuota()?(o.attr("class","progress-bar bg-danger"),r.find(".quota-meter-text").css("color","white"),this.trigger("quota:over",t)):e>=this.options.warnAtPercent?(o.attr("class","progress-bar bg-warning"),this.trigger("quota:under quota:under:approaching",t)):(o.attr("class","progress-bar bg-success"),this.trigger("quota:under quota:under:ok",t)),r},_render_usage:function(){var t=$(this._templateUsage(this.model.toJSON()));return this.log(this+".rendering usage:",t),t},render:function(){var t=null;return this.log(this+".model.quota_percent:",this.model.get("quota_percent")),t=null===this.model.get("quota_percent")||void 0===this.model.get("quota_percent")?this._render_usage():this._render_quota(),this.$el.html(t),this.$el.find(".quota-meter-text").tooltip(),this},_templateQuotaMeter:function(t){return['<div id="quota-meter" class="quota-meter progress">','<div class="progress-bar" style="width: ',t.quota_percent,'%"></div>','<div class="quota-meter-text" data-placement="left" style="top: 6px"',t.nice_total_disk_usage?' title="Using '+t.nice_total_disk_usage+'. Click for details.">':">",'<a href="https://galaxyproject.org/support/account-quotas/" target="_blank">',(0,d.default)("Using")," ",t.quota_percent,"%","</a>","</div>","</div>"].join("")},_templateUsage:function(t){return['<div id="quota-meter" class="quota-meter" style="background-color: transparent">','<div class="quota-meter-text" data-placement="left" data-original-title="This value is recalculated when you log out." style="top: 6px; color: white">',t.nice_total_disk_usage?(0,d.default)("Using ")+t.nice_total_disk_usage:"","</div>","</div>"].join("")},toString:function(){return"UserQuotaMeter("+this.model+")"}});t.default={UserQuotaMeter:c}});