from django.core.management.base import BaseCommand
from django.contrib.auth.models import User, Group

class Command(BaseCommand):
    help = 'إنشاء حسابات فريق إعداد الأسئلة'

    def add_arguments(self, parser):
        parser.add_argument('--اسم_المستخدم', type=str, help='اسم المستخدم للفريق')
        parser.add_argument('--الايميل', type=str, help='البريد الإلكتروني')
        parser.add_argument('--كلمة_المرور', type=str, help='كلمة المرور')

    def handle(self, *args, **options):
        # التأكد من وجود مجموعة فريق إعداد الأسئلة
        فريق_الاسئلة, تم_الانشاء = Group.objects.get_or_create(name='فريق إعداد الأسئلة')
        
        if تم_الانشاء:
            self.stdout.write(
                self.style.SUCCESS('✅ تم إنشاء مجموعة "فريق إعداد الأسئلة"')
            )

        # إنشاء مستخدم جديد إذا تم تمرير البيانات
        if options['اسم_المستخدم'] and options['الايميل'] and options['كلمة_المرور']:
            اسم_المستخدم = options['اسم_المستخدم']
            الايميل = options['الايميل']
            كلمة_المرور = options['كلمة_المرور']
            
            if User.objects.filter(username=اسم_المستخدم).exists():
                self.stdout.write(
                    self.style.ERROR(f'❌ المستخدم {اسم_المستخدم} موجود بالفعل')
                )
                return
            
            # إنشاء المستخدم
            المستخدم = User.objects.create_user(
                username=اسم_المستخدم,
                email=الايميل,
                password=كلمة_المرور,
                is_staff=True,  # يمكنه الدخول لواجهة الإدارة
                is_superuser=False  # ليس مدير كامل
            )
            
            # إضافة المستخدم لمجموعة فريق إعداد الأسئلة
            المستخدم.groups.add(فريق_الاسئلة)
            
            self.stdout.write(
                self.style.SUCCESS(f'🎉 تم إنشاء حساب {اسم_المستخدم} بنجاح!')
            )
            self.stdout.write('📋 صلاحيات العضو الجديد:')
            self.stdout.write('   ✅ إضافة وتعديل أسئلة خلية الحروف')
            self.stdout.write('   ✅ إنشاء حزم جديدة لخلية الحروف')
            self.stdout.write('   ✅ عرض الحزم والأسئلة الموجودة')
            self.stdout.write('')
            self.stdout.write('⛔ لا يمكنه:')
            self.stdout.write('   ❌ حذف أي حزم أو أسئلة')
            self.stdout.write('   ❌ تغيير أسعار الحزم أو تفعيلها')
            self.stdout.write('   ❌ رؤية المشتريات أو الأموال')
            self.stdout.write('   ❌ الوصول لجلسات اللعب')
        else:
            # عرض التعليمات
            self.stdout.write(
                self.style.SUCCESS('📖 تعليمات إنشاء حساب جديد لفريق إعداد الأسئلة:')
            )
            self.stdout.write('')
            self.stdout.write('استخدم الأمر التالي:')
            self.stdout.write(
                'python manage.py انشاء_فريق --اسم_المستخدم احمد --الايميل ahmed@example.com --كلمة_المرور كلمة_سرية'
            )
            self.stdout.write('')
            
        # عرض أعضاء الفريق الحاليين
        اعضاء_الفريق = User.objects.filter(groups=فريق_الاسئلة)
        if اعضاء_الفريق.exists():
            self.stdout.write(
                self.style.SUCCESS('👥 أعضاء فريق إعداد الأسئلة الحاليين:')
            )
            for عضو in اعضاء_الفريق:
                حالة = "🟢 نشط" if عضو.is_active else "🔴 غير نشط"
                self.stdout.write(f'   • {عضو.username} ({عضو.email}) - {حالة}')
        else:
            self.stdout.write(
                self.style.WARNING('📋 لا يوجد أعضاء في فريق إعداد الأسئلة حتى الآن')
            )
            
        self.stdout.write('')
        self.stdout.write(
            self.style.SUCCESS('💡 تذكير: أنت (المدير الكامل) لديك كل الصلاحيات')
        )
        self.stdout.write('   ✅ إدارة الأسعار والتفعيل')
        self.stdout.write('   ✅ حذف الحزم والأسئلة')
        self.stdout.write('   ✅ مراقبة المشتريات والإحصائيات')
        self.stdout.write('   ✅ إدارة جلسات اللعب')