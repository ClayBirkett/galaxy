define(["mvc/user/change-information","mvc/user/change-password","mvc/user/change-permissions","mvc/user/change-api-key","mvc/user/change-toolbox-filter","mvc/user/change-communication"],function(a,b,c,d,e,f){var g=Backbone.View.extend({initialize:function(){this.setElement("<div/>"),this.render()},_link:function(a){var b=this,c=$('<a target="galaxy_main" href="javascript:void(0)">'+a.title+"</a>").on("click",function(){$.ajax({url:Galaxy.root+a.url,type:"GET"}).always(function(c){b.$preferences.hide(),c.onclose=function(){b.$preferences.show()},b.$el.append(new a.module(c).$el)})});this.$pages.append($("<li/>").append(c))},render:function(){var g=this;$.getJSON(Galaxy.root+"api/user_preferences",function(h){if(g.$preferences=$("<div/>"),null!==h.id){if(g.$preferences.append("<h2>User preferences</h2>").append("<p>You are currently logged in as "+_.escape(h.email)+".</p>").append(g.$pages=$("<ul/>")),h.remote_user||(g._link({title:"Manage your information (email, address, etc.)",url:"api/user_preferences/"+Galaxy.user.id+"/information",module:a}),g._link({title:"Change your password",url:"api/user_preferences/"+Galaxy.user.id+"/password",module:b})),"galaxy"==h.webapp?(g._link({title:"Change your communication settings",url:"api/user_preferences/"+Galaxy.user.id+"/communication",module:f}),g._link({title:"Change default permissions for new histories",url:"api/user_preferences/change-permissions",module:c}),g._link({title:"Manage your API keys",url:"api/user_preferences/"+Galaxy.user.id+"/api_key",module:d}),g._link({title:"Manage your ToolBox filters",url:"api/user_preferences/change_toolbox_filters",module:e}),h.openid&&!h.remote_user&&g._link({title:"Manage OpenIDs linked to your account",module:null})):(g._link({title:"Manage your API keys",module:d}),g._link({title:"Manage your email alerts",module:null})),"galaxy"==h.webapp){var i="<p>You are using <strong>"+h.disk_usage+"</strong> of disk space in this Galaxy instance.";h.enable_quotas&&(i+="Your disk quota is: <strong>"+h.quota+"</strong>."),i+='Is your usage more than expected?  See the <a href="https://wiki.galaxyproject.org/Learn/ManagingDatasets" target="_blank">documentation</a> for tips on how to find all of the data in your account.</p>',g.$preferences.append(i)}}else h.message||g.$preferences.append("<p>You are currently not logged in.</p>"),$preferences('<ul><li><a target="galaxy_main">Login</a></li><li><a target="galaxy_main">Register</a></li></ul>');g.$el.empty().append(g.$preferences)})}});return{UserPreferences:g}});
//# sourceMappingURL=../../../maps/mvc/user/user-preferences.js.map